#!/usr/bin/env python
# Copyright (c) 2018-2019 Intel Corporation.
# authors: German Ros (german.ros@intel.com), Felipe Codevilla (felipe.alcm@gmail.com)
#
# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.

"""
CARLA Challenge Evaluator Routes

Provisional code to evaluate Autonomous Agents for the CARLA Autonomous Driving challenge
"""
from __future__ import print_function

import traceback
import argparse
from argparse import RawTextHelpFormatter
from datetime import datetime
from distutils.version import LooseVersion
import importlib
import os
import sys
import gc
import pkg_resources
import sys
import carla
import signal
import torch
import time
import joblib
import dill
import pathlib
import numpy as np
import random
random.seed(3.1415926)


from srunner.scenariomanager.carla_data_provider import *
from srunner.scenariomanager.timer import GameTime
from srunner.scenariomanager.watchdog import Watchdog


from leaderboard.scenarios.scenario_manager import ScenarioManager
from leaderboard.scenarios.route_scenario import RouteScenario
from leaderboard.envs.sensor_interface import SensorInterface, SensorConfigurationInvalid
from leaderboard.autoagents.agent_wrapper import  AgentWrapper, AgentError
from leaderboard.utils.statistics_manager import StatisticsManager
from leaderboard.utils.route_indexer import RouteIndexer
from leaderboard.utils.EXParser import EXParser

from pymoo.core.problem import ElementwiseProblem
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.rnd import FloatRandomSampling
from pymoo.termination import get_termination
from pymoo.optimize import minimize

from SBT.scenario_parser import ego_vehicle_parser, other_vehicle_parser, weather_parser 
from SBT.problem import CustomizedProblem, SurrogateProblem
from SBT.framework import search_based_testing



sensors_to_icons = {
    'sensor.camera.rgb':        'carla_camera',
    'sensor.camera.semantic_segmentation': 'carla_camera',
    'sensor.camera.depth':      'carla_camera',
    'sensor.lidar.ray_cast':    'carla_lidar',
    'sensor.lidar.ray_cast_semantic':    'carla_lidar',
    'sensor.other.radar':       'carla_radar',
    'sensor.other.gnss':        'carla_gnss',
    'sensor.other.imu':         'carla_imu',
    'sensor.opendrive_map':     'carla_opendrive_map',
    'sensor.speedometer':       'carla_speedometer'
}


WEATHERS = {
        'ClearNoon': carla.WeatherParameters.ClearNoon,
        'ClearSunset': carla.WeatherParameters.ClearSunset,

        'CloudyNoon': carla.WeatherParameters.CloudyNoon,
        'CloudySunset': carla.WeatherParameters.CloudySunset,

        'WetNoon': carla.WeatherParameters.WetNoon,
        'WetSunset': carla.WeatherParameters.WetSunset,

        'MidRainyNoon': carla.WeatherParameters.MidRainyNoon,
        'MidRainSunset': carla.WeatherParameters.MidRainSunset,

        'WetCloudyNoon': carla.WeatherParameters.WetCloudyNoon,
        'WetCloudySunset': carla.WeatherParameters.WetCloudySunset,

        'HardRainNoon': carla.WeatherParameters.HardRainNoon,
        'HardRainSunset': carla.WeatherParameters.HardRainSunset,

        'SoftRainNoon': carla.WeatherParameters.SoftRainNoon,
        'SoftRainSunset': carla.WeatherParameters.SoftRainSunset,
}

class TestCase(object):
    ego_vehicles = []

    # Tunable parameters
    client_timeout = 10.0  # in seconds
    wait_for_world = 20.0  # in seconds
    frame_rate = 20.0      # in Hz

    def __init__(self, args, statistics_manager):
        """
        Setup CARLA client and world
        Setup ScenarioManager
        """
        self.statistics_manager = statistics_manager
        self.sensors = None
        self.sensor_icons = []
        self._vehicle_lights = carla.VehicleLightState.Position | carla.VehicleLightState.LowBeam

        # First of all, we need to create the client that will send the requests
        # to the simulator. Here we'll assume the simulator is accepting
        # requests in the localhost at port 2000.
        self.client = carla.Client(args.host, int(args.port))
        self.args = args
        if args.timeout:
            self.client_timeout = float(args.timeout)
        self.client.set_timeout(self.client_timeout)

        try:
            self.traffic_manager = self.client.get_trafficmanager(int(args.trafficManagerPort))
            # self.traffic_manager = self.client.get_trafficmanager(8000)
        except Exception as e:
            print(e)
        dist = pkg_resources.get_distribution("carla")
        # if dist.version != 'leaderboard':
        #     if LooseVersion(dist.version) < LooseVersion('0.9.10'):
        #         raise ImportError("CARLA version 0.9.10.1 or newer required. CARLA version found: {}".format(dist))

        # Load agent
        module_name = os.path.basename(args.agent).split('.')[0]
        sys.path.insert(0, os.path.dirname(args.agent))
        self.module_agent = importlib.import_module(module_name)

        # Create the ScenarioManager
        self.manager = ScenarioManager(args.timeout, args.debug > 1, log=args.log, fitness_path = args.fitness_path)

        # Time control for summary purposes
        self._start_time = GameTime.get_time()
        self._end_time = None

        # Create the agent timer
        self._agent_watchdog = Watchdog(int(float(args.timeout)))
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """
        Terminate scenario ticking when receiving a signal interrupt
        """
        if self._agent_watchdog and not self._agent_watchdog.get_status():
            raise RuntimeError("Timeout: Agent took too long to setup")
        elif self.manager:
            self.manager.signal_handler(signum, frame)

    def __del__(self):
        """
        Cleanup and delete actors, ScenarioManager and CARLA world
        """

        self._cleanup()
        if hasattr(self, 'manager') and self.manager:
            del self.manager
        if hasattr(self, 'world') and self.world:
            del self.world

    def _cleanup(self):
        """
        Remove and destroy all actors
        """

        # Simulation still running and in synchronous mode?
        if self.manager and self.manager.get_running_status() \
                and hasattr(self, 'world') and self.world:
            # Reset to asynchronous mode
            settings = self.world.get_settings()
            settings.synchronous_mode = False
            settings.fixed_delta_seconds = None
            self.world.apply_settings(settings)
            self.traffic_manager.set_synchronous_mode(False)

        if self.manager:
            self.manager.cleanup()

        CarlaDataProvider.cleanup()

        for i, _ in enumerate(self.ego_vehicles):
            if self.ego_vehicles[i]:
                self.ego_vehicles[i].destroy()
                self.ego_vehicles[i] = None
        self.ego_vehicles = []

        if self._agent_watchdog:
            self._agent_watchdog.stop()

        if hasattr(self, 'agent_instance') and self.agent_instance:
            self.agent_instance.destroy()
            self.agent_instance = None

        if hasattr(self, 'statistics_manager') and self.statistics_manager:
            self.statistics_manager.scenario = None

    def _prepare_ego_vehicles(self, ego_vehicles, wait_for_ego_vehicles=False):
        """
        Spawn or update the ego vehicles
        """

        if not wait_for_ego_vehicles:
            for vehicle in ego_vehicles:
                self.ego_vehicles.append(CarlaDataProvider.request_new_actor(vehicle.model,
                                                                             vehicle.transform,
                                                                             vehicle.rolename,
                                                                             color=vehicle.color,
                                                                             vehicle_category=vehicle.category))

        else:
            ego_vehicle_missing = True
            while ego_vehicle_missing:
                self.ego_vehicles = []
                ego_vehicle_missing = False
                for ego_vehicle in ego_vehicles:
                    ego_vehicle_found = False
                    carla_vehicles = CarlaDataProvider.get_world().get_actors().filter('vehicle.*')
                    for carla_vehicle in carla_vehicles:
                        if carla_vehicle.attributes['role_name'] == ego_vehicle.rolename:
                            ego_vehicle_found = True
                            self.ego_vehicles.append(carla_vehicle)
                            break
                    if not ego_vehicle_found:
                        ego_vehicle_missing = True
                        break

            for i, _ in enumerate(self.ego_vehicles):
                self.ego_vehicles[i].set_transform(ego_vehicles[i].transform)

        # sync state
        # print(CarlaDataProvider.get_world().get_actors().filter('vehicle.*'))
        CarlaDataProvider.get_world().tick()

    def _load_and_wait_for_world(self, args, town, weather):
        """
        Load a new CARLA world and provide data to CarlaDataProvider
        """
        self.traffic_manager.set_synchronous_mode(False)
        # if hasattr(self, 'world'):
        #     settings = self.world.get_settings()
        #     settings.synchronous_mode = False
        #     self.world.apply_settings(settings)
        if self.args.log:
            print(town)
        try: 
            self.world = self.client.load_world(town)
        except Exception as e:
            print(e)
        settings = self.world.get_settings()
        settings.fixed_delta_seconds = 1.0 / self.frame_rate
        settings.synchronous_mode = True
        self.world.apply_settings(settings)

        self.world.reset_all_traffic_lights()

        # print(weather)
        CarlaDataProvider.set_client(self.client)
        CarlaDataProvider.set_world(self.world)
        CarlaDataProvider.set_traffic_manager_port(int(args.trafficManagerPort))
        CarlaDataProvider.set_weather(weather)

        self.traffic_manager.set_synchronous_mode(True)
        self.traffic_manager.set_random_device_seed(int(args.trafficManagerSeed))

        # Wait for the world to be ready
        if CarlaDataProvider.is_sync_mode():
            self.world.tick()
        else:
            self.world.wait_for_tick()

        if CarlaDataProvider.get_map().name != town:
            raise Exception("The CARLA server uses the wrong map!"
                            "This scenario requires to use map {}".format(town))

    def _register_statistics(self, config, checkpoint, entry_status, crash_message=""):
        """
        Computes and saved the simulation statistics
        """
        # register statistics
        current_stats_record = self.statistics_manager.compute_route_statistics(
            config,
            self.manager.scenario_duration_system,
            self.manager.scenario_duration_game,
            crash_message
        )
        if self.args.log:
            print("\033[1m> Registering the route statistics\033[0m")
        self.statistics_manager.save_record(current_stats_record, config.index, checkpoint)
        self.statistics_manager.save_entry_status(entry_status, False, checkpoint)

    def _fill_lane(self, lane_waypoint, region=7, padding = 4):
                
        # new_vehicles = [lane_waypoint]
        new_vehicles = []
        
        max_move = int((region-padding)/2)

        # next_waypoint = lane_waypoint
        # count = 0
        # new_vehicles += lane_waypoint.next_until_lane_end(region)

        next_waypoint = lane_waypoint
        while (not next_waypoint.is_junction) and next_waypoint.road_id == lane_waypoint.road_id:
            # next_waypoint = next_waypoint.next(random.randint(padding, 2*region-padding))[0]
            next_waypoint = next_waypoint.next(region)[0]
            new_vehicles.append(next_waypoint)
        new_vehicles = new_vehicles[:-1]
        # new_vehicles += lane_waypoint.previous_until_lane_start(region)

        previous_waypoint = lane_waypoint
        while (not previous_waypoint.is_junction) and previous_waypoint.road_id == lane_waypoint.road_id:
            # previous_waypoint = previous_waypoint.previous(random.randint(padding, 2*region-padding))[0]
            previous_waypoint = previous_waypoint.previous(region)[0]
            new_vehicles.append(previous_waypoint)
        new_vehicles = new_vehicles[:-1]
        
        # while next_waypoint and count < 20:
        #     next_waypoint = next_waypoint.next(region)[-1]
        #     new_vehicles.append(next_waypoint)
        #     count += 1

        # next_waypoint = lane_waypoint
        # count = 0
        # while next_waypoint and count < 20:
        #     next_waypoint = next_waypoint.previous(region)[-1]
        #     new_vehicles.append(next_waypoint)
        #     count += 1
        new_vehicles = [self._random_move_waypoint(waypoint, max_move) for waypoint in new_vehicles] + [lane_waypoint]
        # print('{}\t{}\t{}\t{}'.format(lane_waypoint.lane_id, lane_waypoint.road_id, max_move, len(new_vehicles), lane_waypoint.transform.location))
        return new_vehicles
    
    def _random_move_waypoint(self, waypoint, max_move):
        move = random.randint(-max_move, max_move)
        if move == 0:
            return waypoint
        elif move<0:
            return waypoint.previous(-move)[0]
        else:
            return waypoint.next(move)[0]

    def _fill_road(self, road_waypoint, region=7):
        # waypoint = road_waypoint
        # print()
        # print(waypoint.lane_id, waypoint.road_id, waypoint.lane_change, waypoint.lane_type, waypoint.left_lane_marking.type, waypoint.right_lane_marking.type)
        # waypoint = road_waypoint.get_left_lane()
        # print(waypoint.lane_id, waypoint.road_id, waypoint.lane_change, waypoint.lane_type, waypoint.left_lane_marking.type, waypoint.right_lane_marking.type)
        # waypoint = road_waypoint.get_right_lane()
        # print(waypoint.lane_id, waypoint.road_id, waypoint.lane_change, waypoint.lane_type, waypoint.left_lane_marking.type, waypoint.right_lane_marking.type)
        # print()

        waypoints = []
        lanes = [road_waypoint.lane_id]
        road_id = road_waypoint.road_id
        lane_stack = [road_waypoint]
        while lane_stack:
            waypoint = lane_stack.pop()
            # print(waypoint.lane_id, waypoint.road_id, waypoint.lane_type, waypoint)
            if str(waypoint.lane_type) == 'Driving':
                waypoints.append(waypoint)
            next_waypoints = [waypoint.get_left_lane(), waypoint.get_right_lane()]
            for next_waypoint in next_waypoints:
                if next_waypoint:
                    if next_waypoint.road_id == road_id:
                        if next_waypoint.lane_id not in lanes:
                            lanes.append(next_waypoint.lane_id)
                            lane_stack.append(next_waypoint)
            # print(lanes)
            # print([w.lane_id for w in waypoints])
            # print([w.lane_id for w in lane_stack])
            # print()

        new_vehicles = []
        for waypoint in waypoints:
            new_vehicles += self._fill_lane(waypoint, region)

        return new_vehicles

    def _get_road_by_junction(self, junction, filted_road):
        roads = [filted_road]
        road_waypoints = []

        lane_in_junc =  junction.get_waypoints(carla.LaneType.Driving)
        for waypoint,_ in lane_in_junc:
            # junc_road_id = waypoint.road_id
            next_waypoint = waypoint
            while next_waypoint.is_junction:
                next_waypoint = next_waypoint.next(1)[0]
            if next_waypoint.road_id not in roads:
                roads.append(next_waypoint.road_id)
                road_waypoints.append(next_waypoint)

            previous_waypoint = waypoint
            while previous_waypoint.is_junction:
                previous_waypoint = previous_waypoint.previous(1)[0]
            if previous_waypoint.road_id not in roads:
                roads.append(previous_waypoint.road_id)
                road_waypoints.append(previous_waypoint)

        # for waypoint in road_waypoints:
        #     print(waypoint.lane_id, waypoint.road_id, waypoint.lane_change, waypoint.lane_type, waypoint.is_junction, str(waypoint.transform.location))

        return road_waypoints

    def _fill_junction(self, road_waypoint, region=7):
        roads = []
        road_waypoints = [road_waypoint]

        # print(road_waypoint.lane_id, road_waypoint.road_id, road_waypoint.lane_change, road_waypoint.lane_type, road_waypoint.is_junction)

        end_junc_waypoint = road_waypoint
        while not end_junc_waypoint.is_junction:
            end_junc_waypoint = end_junc_waypoint.next(1)[0]
        # print(end_junc_waypoint.lane_id, end_junc_waypoint.road_id, end_junc_waypoint.lane_change, end_junc_waypoint.lane_type, end_junc_waypoint.is_junction)

        road_waypoints += self._get_road_by_junction(end_junc_waypoint.get_junction(), road_waypoint.road_id)

        # for waypoint,waypoint1 in end_junc_waypoint.get_junction().get_waypoints(carla.LaneType.Driving):
        #     print(waypoint.lane_id, waypoint.road_id, waypoint.lane_change, waypoint.lane_type, waypoint.is_junction, str(waypoint.transform.location))
        #     print(waypoint1.lane_id, waypoint1.road_id, waypoint1.lane_change, waypoint1.lane_type, waypoint1.is_junction, str(waypoint1.transform.location))
        #     print()

        # start_junc_waypoint = road_waypoint.previous_until_lane_start(1)[-1].previous(1)[0]
        start_junc_waypoint = road_waypoint
        while not start_junc_waypoint.is_junction:
            start_junc_waypoint = start_junc_waypoint.previous(1)[0]
        # print(start_junc_waypoint.lane_id, start_junc_waypoint.road_id, start_junc_waypoint.lane_change, start_junc_waypoint.lane_type, start_junc_waypoint.is_junction)
        
        road_waypoints += self._get_road_by_junction(start_junc_waypoint.get_junction(), road_waypoint.road_id)

        # print()
        # print(str(road_waypoint.transform.location))
        # print(str(end_junc_waypoint.transform.location))
        # print([str(waypoint.transform.location) for waypoint in road_waypoint.next_until_lane_end(1)])

        # print()
        # print(str(road_waypoint.transform.location))
        # print(str(start_junc_waypoint.transform.location))
        # print([str(waypoint.transform.location) for waypoint in road_waypoint.previous_until_lane_start(1)])

        new_vehicles = []
        for road_waypoint in road_waypoints:
            new_vehicles += self._fill_road(road_waypoint, region)
        print('# of added vehicles:',len(new_vehicles))
        return new_vehicles

    def _load_and_run_scenario(self, args, config):
        """
        Load and run the scenario given by config.

        Depending on what code fails, the simulation will either stop the route and
        continue from the next one, or report a crash and stop.
        """
        crash_message = ""
        entry_status = "Started"
        config.timeout = args.timeout
        if self.args.log:
            print("\n\033[1m========= Preparing {} (repetition {}) =========".format(config.name, config.repetition_index))
            print("> Setting up the agent\033[0m")

        # Prepare the statistics of the route
        self.statistics_manager.set_route(config.name, config.index)
        # Set up the user's agent, and the timer to avoid freezing the simulation
        try:
            self._agent_watchdog.start()
            agent_class_name = getattr(self.module_agent, 'get_entry_point')()
            self.agent_instance = getattr(self.module_agent, agent_class_name)(args.agent_config)
            config.agent = self.agent_instance

            # Check and store the sensors
            if not self.sensors:
                self.sensors = self.agent_instance.sensors()
                track = self.agent_instance.track

                AgentWrapper.validate_sensor_configuration(self.sensors, track, args.track)

                self.sensor_icons = [sensors_to_icons[sensor['type']] for sensor in self.sensors]
                self.statistics_manager.save_sensors(self.sensor_icons, args.checkpoint)

            self._agent_watchdog.stop()
        except SensorConfigurationInvalid as e:
            # The sensors are invalid -> set the ejecution to rejected and stop
            print("\n\033[91mThe sensor's configuration used is invalid:")
            print("> {}\033[0m\n".format(e))
            traceback.print_exc()

            crash_message = "Agent's sensors were invalid"
            entry_status = "Rejected"

            self._register_statistics(config, args.checkpoint, entry_status, crash_message)
            self._cleanup()
            sys.exit(-1)
        except Exception as e:
            # The agent setup has failed -> start the next route
            print("\n\033[91mCould not set up the required agent:")
            print("> {}\033[0m\n".format(e))
            traceback.print_exc()

            crash_message = "Agent couldn't be set up"

            self._register_statistics(config, args.checkpoint, entry_status, crash_message)
            self._cleanup()
            return

        if self.args.log:
            print("\033[1m> Loading the world\033[0m")
        
        # Load the world and the scenario
        try:
            self._load_and_wait_for_world(args, config.town, config.weather)
            self._prepare_ego_vehicles(config.ego_vehicles, False) # Other vehichle is still not added into the scneario
            
            # Change the start and end position of the ego-vehicle
            current_map = CarlaDataProvider.get_map()
            # start_location, end_location = config.trajectory
            config.trajectory = ego_vehicle_parser(config.original_trajectory, config.ego_vehicle_vec, current_map)
            # ego_vehicle_parser(config.trajectory, config.ego_vehicle_vec, current_map)
            start_location, end_location = config.trajectory


            scenario = None
            if args.agent_mode == 1:
                print("AGENT_MODE == 1")
                waypoints = self.get_waypoints(start_location)
                scenario = RouteScenario(world=self.world, config=config, debug_mode=args.debug, agent_mode=args.agent_mode, waypoints=waypoints)# add vehicle in this line
            elif args.agent_mode == 0:
                # current_map = CarlaDataProvider.get_map()

                # print('+++++++++++++++++')
                # all_roads = current_map.get_topology() 
                # print(len(all_roads))
                # print('  Road     Lane |     x        y   |     x        y   |        yaw       ')
                # print('-------------------------------------------------------------------------')
                # for start, end in all_roads:
                #     print('{:4} {:4} {:2} {:2} | {:7.2f}, {:7.2f} | {:7.2f}, {:7.2f} | {:7.2f}, {:7.2f} '.format(start.road_id, end.road_id, 
                #                                         start.lane_id, end.lane_id, 
                #                                         start.transform.location.x, start.transform.location.y, 
                #                                         end.transform.location.x, end.transform.location.y,
                #                                         start.transform.rotation.yaw, end.transform.rotation.yaw))
                #     # self._get_road_from_start(start)
                # print('+++++++++++++++++')

                # # Print all roads
                # for start, end in all_roads:
                #     road = self._get_road_from_start(start)
                #     self._draw_road(self.world, start, road, 
                #                     vertical_shift=1.0, persistency=50000.0)

                current_waypoint = current_map.get_waypoint(start_location)
                if self.args.log:
                    print('##current vehicle:', current_waypoint)

                numb_other_vehicle = 0
                waypoint_other_vehicle = []

                ## region = 7
                ## region = 14
                ## region = 20
                region = args.region

                if region == 0:
                    pass
                
                elif region < 0:
                    
                    config.vehicle_infront = True
                    config.vehicle_side = True
                    config.vehicle_opposite = True

                    if config.vehicle_infront:
                        test_waypoint = current_waypoint.next(7)[-1]

                        if test_waypoint:
                            numb_other_vehicle += 1
                            if self.args.log:
                                print('----VEHICLE-INFRONT----')
                            # print('##current vehicle:', current_waypoint)
                            # print('####other vehicle:', test_waypoint)

                            waypoint_other_vehicle.append(test_waypoint)
                        else:
                            if self.args.log:
                                print("!!!!! Add vehicle infront failed")

                    if config.vehicle_side:
                        test_waypoint = current_waypoint.get_right_lane()

                        if test_waypoint:
                            numb_other_vehicle += 1
                            if self.args.log:
                                print('----VEHICLE-SIDE-------')
                            # print('##current vehicle:', current_waypoint)
                            # print('####other vehicle:', test_waypoint)

                            road = self._get_road(test_waypoint)
                            road_start = road[0]

                            waypoint_other_vehicle.append(road_start)
                            # self._draw_road(self.world, test_waypoint, road, 
                            #             vertical_shift=1.0, persistency=50000.0)
                        else:
                            if self.args.log:
                                print("!!!!! Add vehicle in side lane failed")
                        
                    if config.vehicle_opposite:
                        test_waypoint = current_waypoint.get_left_lane()
                        
                        while True:
                            # print(test_waypoint.lane_type, type(test_waypoint.lane_type))
                            # print(test_waypoint.lane_id, current_waypoint.lane_id)
                            # print(test_waypoint.lane_type, current_waypoint.lane_type)
                            if not test_waypoint:
                                break
                            if test_waypoint.lane_type == carla.LaneType.Bidirectional:
                                # print(carla.LaneType.Bidirectional)
                                test_waypoint = test_waypoint.get_right_lane()
                            if test_waypoint.lane_id > 0 == current_waypoint.lane_id > 0: 
                                test_waypoint = test_waypoint.get_right_lane()
                            else:
                                break

                        if test_waypoint:
                            numb_other_vehicle += 1
                            if self.args.log:
                                print('----VEHICLE-OPPOSITE---')
                            # print('##current vehicle:', current_waypoint)
                            # print('####other vehicle:', test_waypoint)

                            road = self._get_road(test_waypoint)

                            road_end = road[-1]
                            road_start = road[0]

                            waypoint_other_vehicle.append(road_start)
                            # self._draw_road(self.world, test_waypoint, road, 
                            #                 vertical_shift=1.0, persistency=50000.0)
                        else:
                            if self.args.log:
                                print("!!!!! Add vehicle in opposite lane failed")

                else:
                    waypoint_other_vehicle = self._fill_junction(current_waypoint, region)
                    numb_other_vehicle = len(waypoint_other_vehicle)

                scenario = RouteScenario(world=self.world, 
                                        config=config, 
                                        debug_mode=args.debug, 
                                        agent_mode=args.agent_mode, 
                                        numb_other_vehicle=numb_other_vehicle,
                                        start_waypoint=waypoint_other_vehicle)# add vehicle in this line
            self.statistics_manager.set_scenario(scenario.scenario)


            # Night mode
            if config.weather.sun_altitude_angle < 0.0:
                for vehicle in scenario.ego_vehicles:
                    vehicle.set_light_state(carla.VehicleLightState(self._vehicle_lights))

            # Load scenario and run it
            if args.record:
                self.client.start_recorder("{}/{}_rep{}.log".format(args.record, config.name, config.repetition_index))
            self.manager.load_scenario(scenario, self.agent_instance, config.repetition_index)

            # self.log_xml(args, config, 
            #              [scenario.ego_vehicle]+scenario.other_actors, 
            #              [config.trajectory,waypoints])

        except Exception as e:
            # The scenario is wrong -> set the ejecution to crashed and stop
            print("\n\033[91mThe scenario could not be loaded:")
            print("> {}\033[0m\n".format(e))
            traceback.print_exc()

            crash_message = "Simulation crashed"
            entry_status = "Crashed"

            self._register_statistics(config, args.checkpoint, entry_status, crash_message)

            if args.record:
                self.client.stop_recorder()

            self._cleanup()
            sys.exit(-1)

        if self.args.log:
            print("\033[1m> Running the route\033[0m")
        traffic_manager = CarlaDataProvider.get_client().get_trafficmanager(CarlaDataProvider.get_traffic_manager_port()) 
        traffic_manager.global_percentage_speed_difference(-75) # SPEED. base speed 30 km/h, increased by 50%

        self.manager.run_scenario()

        try:
            if self.args.log:
                print("\033[1m> Stopping the route\033[0m")
            self.manager.stop_scenario()
            self._register_statistics(config, args.checkpoint, entry_status, crash_message)

            if args.record:
                self.client.stop_recorder()

            # Remove all actors
            scenario.remove_all_actors()

            self._cleanup()

        except Exception as e:
            print("\n\033[91mFailed to stop the scenario, the statistics might be empty:")
            print("> {}\033[0m\n".format(e))
            traceback.print_exc()

            crash_message = "Simulation crashed"

        if crash_message == "Simulation crashed":
            print('***Simulation crashed***')
            sys.exit(-1)

    def _get_road(self, current_waypoint, gap = 3):
        '''
        Provide a waypoint, return all waypoints on this straight road (section bewteen 2 junctions)
        '''
        road = [current_waypoint]

        pre_waypoint = current_waypoint
        while True:
            pre_waypoint = pre_waypoint.previous(gap)
            if not pre_waypoint:
                break
            elif pre_waypoint[-1].is_junction:
                break
            else:
                pre_waypoint = pre_waypoint[-1]
                road.append(pre_waypoint)
        road.reverse()

        next_waypoint = current_waypoint
        while True:
            next_waypoint = next_waypoint.next(gap)
            if not next_waypoint:
                break
            elif next_waypoint[-1].is_junction:
                break
            else:
                next_waypoint = next_waypoint[-1]
                road.append(next_waypoint)

        return road
    
    def _get_road_from_start(self, start, gap = 3):
        '''
        Provide a waypoint, return all waypoints on this straight road (section bewteen 2 junctions)
        '''
        road = [start]

        next_waypoint = start
        while True:
            next_waypoint = next_waypoint.next(gap)
            if not next_waypoint: # end of road
                break
            next_waypoint = next_waypoint[-1]
            x_same = next_waypoint.transform.location.x == start.transform.location.x
            y_same = next_waypoint.transform.location.y == start.transform.location.y
            z_same = next_waypoint.transform.location.z == start.transform.location.z
            
            if x_same & y_same & z_same: # road loop
                break
            elif next_waypoint.road_id != start.road_id:
                break
            else:
                road.append(next_waypoint)

        return road
 
    def _draw_road(self, world, current_waypoint, road, vertical_shift, persistency=-1):
        """
        Draw a list of waypoints at a certain height given in vertical_shift.
        """
        for w in road:
            wp = w.transform.location + carla.Location(z=vertical_shift)
            color = carla.Color(0, 255, 0) # Green
            size = 0.1

            world.debug.draw_point(wp, size=size, color=color, life_time=persistency)

        world.debug.draw_point(current_waypoint.transform.location + carla.Location(z=vertical_shift), size=0.15,
                                color=carla.Color(0, 0, 255), life_time=persistency)
        world.debug.draw_point(road[0].transform.location + carla.Location(z=vertical_shift), size=0.3,
                                color=carla.Color(0, 0, 255), life_time=persistency)
        world.debug.draw_point(road[-1].transform.location + carla.Location(z=vertical_shift), size=0.2,
                                color=carla.Color(255, 0, 0), life_time=persistency)
        
    def get_waypoints(self, start_location):
        
        actor_location = carla.Location(start_location.x - 55,
				     					start_location.y + 4,
										start_location.z)

        start_location = actor_location
        waypoints = [start_location]

        import numpy as np

        y_list = (np.random.rand(9)*2-1)*3
        x_list = np.arange(9)*8

        last_x, last_y = start_location.x, start_location.y
        for i, y in enumerate(y_list):
            y += start_location.y
            x = x_list[i] + start_location.x
            
            for j in range(1, 6):
                new_location = carla.Location(last_x+(x-last_x)/6*j,
                                              last_y+(y-last_y)/6*j,
                                              start_location.z)
                waypoints.append(new_location)
            new_location = carla.Location(x, y, start_location.z)
            waypoints.append(new_location)
            last_x, last_y = x, y
     
        return waypoints

    def run(self, args):
        """
        Run the challenge mode
        """
        
        route_indexer = RouteIndexer(args.routes, args.scenarios, args.repetitions)

        if args.resume:
            route_indexer.resume(args.checkpoint)
            self.statistics_manager.resume(args.checkpoint)
        else:
            self.statistics_manager.clear_record(args.checkpoint)
            route_indexer.save_state(args.checkpoint)

        while route_indexer.peek():
            # setup
            config = route_indexer.next()

            # for attr in dir(config):
            # 	if not attr.startswith('__'):
            #         if attr != 'trajectory':
            #             print('\033[92m>{}\033[0m      	  \t| {}'.format(attr, getattr(config, attr)))
            #         else:
            #             for item in getattr(config, attr):
            #                 print('\033[92m>{}\033[0m      	  \t| {}'.format(attr, item))
            # config get here just include the settings from the xml
            # currently xml doesot include vehicle and other information
            # thus config doesnot include them

            import numpy as np
            
            case_number = 1000

            scenario_vecs = np.random.rand(case_number, 9+3+2)
            # 9 weather, 3 other vehicle, 2 position offset

            config.original_trajectory = [config.trajectory[0], config.trajectory[1]]


            for i, scenario_vec in enumerate(scenario_vecs):
                config.repetition_index = i
                print()
                start_time = time.time() 
                print('SCENARIO:',[round(vec,2) for vec in scenario_vec])

                config.weather_vec = scenario_vec[0:9]
                config.other_vehicle_vec = scenario_vec[9:9+3]
                config.ego_vehicle_vec = scenario_vec[9+3:9+3+2] #update should be later, as we donot have map in it
                # config.other_vehicle_vec = [1,1,1]
                # config.ego_vehicle_vec = [1,0]
                # config.weather_vec  = [0,0,0,0,0,0,0,0,0]
                print(config.weather_vec, config.other_vehicle_vec)


                config.vehicle_infront, config.vehicle_opposite, config.vehicle_side = other_vehicle_parser(config.other_vehicle_vec)
                config.weather = weather_parser(config.weather_vec)
                print()
                # print(config.weather)
                print('Start:', config.trajectory[0])
                print('End  :', config.trajectory[1])
                # print(config.vehicle_infront, config.vehicle_opposite, config.vehicle_side)

                # run
                self._load_and_run_scenario(args, config)

                route_indexer.save_state(args.checkpoint)

                vec_writer = open(args.fitness_path.replace('fitness.csv','scenario.csv'),'a')
                vec_writer.write(','.join([str(vec) for vec in scenario_vec])+'\n')
                vec_writer.close()
                end_time = time.time()
                elapsed_time = end_time - start_time 
                print(f"Processing Time: {elapsed_time:.2f} seconds")

        # save global statistics
        print("\033[1m> Registering the global statistics\033[0m")
        global_stats_record = self.statistics_manager.compute_global_statistics(route_indexer.total)
        StatisticsManager.save_global_record(global_stats_record, self.sensor_icons, route_indexer.total, args.checkpoint)

    def run_one_case(self, scenario_vec, config):
        """
        Run the challenge mode
        """
        # print('run_one_case')

        # 9 weather, 3 other vehicle, 2 position offset
        config.repetition_index = 0
        start_time = time.time() 

        config.weather_vec = scenario_vec[0:9]
        config.other_vehicle_vec = scenario_vec[9:9+3]
        config.ego_vehicle_vec = scenario_vec[9+3:9+3+2] #update should be later, as we donot have map in it
        
        config.other_vehicle_vec = [1,1,1]

        config.vehicle_infront, config.vehicle_opposite, config.vehicle_side = other_vehicle_parser(config.other_vehicle_vec)
        config.weather = weather_parser(config.weather_vec)
        # print(config.weather)

        if self.args.log:
            print()
            # print(config.weather)
            print('Start:', config.trajectory[0])
            print('End  :', config.trajectory[1])
            # print(config.vehicle_infront, config.vehicle_opposite, config.vehicle_side)

        # run
        self._load_and_run_scenario(self.args, config)

        vec_writer = open(self.args.fitness_path.replace('fitness.csv','scenario.csv'),'a')
        vec_writer.write(','.join([str(vec) for vec in scenario_vec])+'\n')
        vec_writer.close()
        # end_time = time.time()
        # elapsed_time = end_time - start_time 
        # print(f"Processing Time: {elapsed_time:.2f} seconds")



def mkdir(path):
    # print(path)
    folder = os.path.exists(path)
    if not folder:
        os.makedirs(path)

def log_experiment_configs(json_path):
    import json

    ## Experiment Controls
    AGENT_MODE = int(os.environ.get('AGENT_MODE', float('inf')))
    ADS_MODEL = os.environ['ADS_MODEL']==True
    GA = os.environ['GA']==True
    SURROGATE = os.environ['SURROGATE']==True
    SURROGATE_MODEL = os.environ.get('SURROGATE_MODEL', '')
    TIMEOUT = int(os.environ.get('TIMEOUT', float('inf')))
    REGION = int(os.environ.get('REGION', 7))
              
    ## Information Collection
    SAVE_IMG = os.environ['SAVE_IMG']==True
    LOG = os.environ['LOG']==True
    SAVE_PATH = os.environ.get('SAVE_PATH', '')

    ## Route File
    ROUTE_FILE = os.environ.get('ROUTE_FILE', '')

    data = {
        'AGENT_MODE' : AGENT_MODE,
        'ADS_MODEL' : ADS_MODEL,
        'GA' : GA,
        'SURROGATE' : SURROGATE,
        'SURROGATE_MODEL' : SURROGATE_MODEL,
        'TIMEOUT' : TIMEOUT,
        'REGION' : REGION,
        'SAVE_IMG' : SAVE_IMG,
        'LOG' : LOG,
        'SAVE_PATH' : SAVE_PATH,
        'ROUTE_FILE' : ROUTE_FILE
    }

    with open(json_path, 'w') as json_file:
        json.dump(data, json_file)

    print("Log experiment configs")


def main():
    description = "CARLA AD Leaderboard Evaluation: evaluate your Agent in CARLA scenarios\n"

    # general parameters
    parser = argparse.ArgumentParser(description=description, formatter_class=RawTextHelpFormatter)
    parser.add_argument('--host', default='localhost',
                        help='IP of the host server (default: localhost)')
    parser.add_argument('--port', default='62000', help='TCP port to listen to (default: 2000)')
    parser.add_argument('--trafficManagerPort', default='8000',
                        help='Port to use for the TrafficManager (default: 8000)')
    parser.add_argument('--trafficManagerSeed', default='0',
                        help='Seed used by the TrafficManager (default: 0)')
    parser.add_argument('--debug', type=int, help='Run with debug output', default=0)
    parser.add_argument('--record', type=str, default='',
                        help='Use CARLA recording feature to create a recording of the scenario')
    parser.add_argument('--timeout', default=60.0, type=int,
                        help='Set the CARLA client timeout value in seconds')
    parser.add_argument('--log', default="1",
                        help='Whether print log to console')

    # simulation setup
    parser.add_argument('--routes',
                        help='Name of the route to be executed. Point to the route_xml_file to be executed.',
                        required=True)
    parser.add_argument('--weather',
                        type=str, default='none',
                        help='Name of the weahter to be executed',
                        )
    parser.add_argument('--scenarios',
                        help='Name of the scenario annotation file to be mixed with the route.',
                        required=True)
    parser.add_argument('--repetitions',
                        type=int,
                        default=1,
                        help='Number of repetitions per route.')

    # agent-related options
    parser.add_argument("-a", "--agent", type=str, help="Path to Agent's py file to evaluate", required=True)
    parser.add_argument("--agent-config", type=str, help="Path to Agent's configuration file", default="")

    parser.add_argument("--track", type=str, default='SENSORS', help="Participation track: SENSORS, MAP")
    parser.add_argument('--resume', type=bool, default=False, help='Resume execution from last checkpoint?')
    parser.add_argument("--checkpoint", type=str,
                        default='./simulation_results.json',
                        help="Path to checkpoint used for saving statistics and resuming")
    parser.add_argument("--fitness_path", type=str,
                        default='./fitness.csv',
                        help="Path for fitness.csv")
    parser.add_argument('--agent_mode', type=int, help='Run with debug output', default=1)

    arguments = parser.parse_args()
    print("init statistics_manager")
    
    surrogate = os.environ['SURROGATE']==True
    arguments.log = os.environ['LOG']==True
    pathlib.Path(os.environ['SAVE_PATH']).mkdir()

    log_experiment_configs(os.environ['SAVE_PATH']+'experiment_config.json')

    statistics_manager = StatisticsManager()
    route_indexer = RouteIndexer(arguments.routes, arguments.scenarios, arguments.repetitions)
    leaderboard_evaluator = TestCase(arguments, statistics_manager) if not surrogate else None

    search_based_testing(arguments, leaderboard_evaluator, route_indexer)

if __name__ == '__main__':
    main()

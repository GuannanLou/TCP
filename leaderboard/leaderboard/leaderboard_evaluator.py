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
import copy

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

class LeaderboardEvaluator(object):

    """
    TODO: document me!
    """

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
        self.manager = ScenarioManager(args.timeout, args.debug > 1, args.fitness_path)

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

    def _load_and_wait_for_world(self, args, town, ego_vehicles=None):
        """
        Load a new CARLA world and provide data to CarlaDataProvider
        """
        self.traffic_manager.set_synchronous_mode(False)
        # if hasattr(self, 'world'):
        #     settings = self.world.get_settings()
        #     settings.synchronous_mode = False
        #     self.world.apply_settings(settings)
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

        CarlaDataProvider.set_client(self.client)
        CarlaDataProvider.set_world(self.world)
        CarlaDataProvider.set_traffic_manager_port(int(args.trafficManagerPort))

        if args.weather != "none":
            assert args.weather in WEATHERS
            CarlaDataProvider.set_weather(WEATHERS[args.weather])

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

        print("\033[1m> Registering the route statistics\033[0m")
        self.statistics_manager.save_record(current_stats_record, config.index, checkpoint)
        self.statistics_manager.save_entry_status(entry_status, False, checkpoint)

    def _load_and_run_scenario(self, args, config):
        """
        Load and run the scenario given by config.

        Depending on what code fails, the simulation will either stop the route and
        continue from the next one, or report a crash and stop.
        """
        crash_message = ""
        entry_status = "Started"

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

        print("\033[1m> Loading the world\033[0m")

        # Load the world and the scenario
        try:
            self._load_and_wait_for_world(args, config.town, config.ego_vehicles)
            self._prepare_ego_vehicles(config.ego_vehicles, False) # Other vehichle is still not added into the scneario

            scenario = None
            if args.agent_mode == 1:
                print("AGENT_MODE == 1")
                start_location, end_location = config.trajectory
                waypoints = self.get_waypoints(start_location)
                scenario = RouteScenario(world=self.world, config=config, debug_mode=args.debug, agent_mode=args.agent_mode, waypoints=waypoints)# add vehicle in this line
            elif args.agent_mode == 0:
                start_location, end_location = config.trajectory
                current_map = CarlaDataProvider.get_map()
                current_waypoint = current_map.get_waypoint(start_location)

                test_waypoint = current_waypoint.get_left_lane()
                # test_waypoint = current_waypoint.get_left_lane().get_right_lane()
                # test_waypoint = current_waypoint.get_left_lane().get_right_lane().get_right_lane()
                
                print('current vehicle:', current_waypoint)
                print('  other vehicle:', test_waypoint)

                road = self._get_road(current_map, test_waypoint)

                road_end = road[-1]
                road_start = road[0]

                # print(road_start)
                # print(test_waypoint)
                # print(road_end)

                self._draw_road(self.world, test_waypoint, road, 
                                vertical_shift=1.0, persistency=50000.0)
                # print('++++++++++++++++++++++')


                print('leader', road_start)
                scenario = RouteScenario(world=self.world, 
                                         config=config, 
                                         debug_mode=args.debug, 
                                         agent_mode=args.agent_mode, 
                                         start_waypoint=[road_start])# add vehicle in this line
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

        print("\033[1m> Running the route\033[0m")

        # Run the scenario
        # try:
        self.manager.run_scenario()

        # except AgentError as e:
        #     # The agent has failed -> stop the route
        #     print("\n\033[91mStopping the route, the agent has crashed:")
        #     print("> {}\033[0m\n".format(e))
        #     traceback.print_exc()

        #     crash_message = "Agent crashed"

        # except Exception as e:
        #     print("\n\033[91mError during the simulation:")
        #     print("> {}\033[0m\n".format(e))
        #     traceback.print_exc()

        #     crash_message = "Simulation crashed"
        #     entry_status = "Crashed"

        # Stop the scenario
        try:
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
            sys.exit(-1)

    
    def _get_road(self, current_map, current_waypoint):
        '''
        Provide a waypoint, return all waypoints on this straight road (section bewteen 2 junctions)
        '''
        road_id = current_waypoint.road_id
        lane_type = current_waypoint.lane_type
        # last_location = current_waypoint.transform.location
        # yaw = current_waypoint.transform.rotation.yaw
        # i = 1
        # gap = 3
        # print()
        # while True:
        #     print(last_location)
        #     if yaw >= 315:   #0
        #         next_location = carla.Location(x=last_location.x - i*gap, y=last_location.y, z=last_location.z)
        #     elif yaw >= 225: #270
        #         next_location = carla.Location(x=last_location.x, y=last_location.y + i*gap, z=last_location.z)
        #     elif yaw >= 135: #180
        #         next_location = carla.Location(x=last_location.x + i*gap, y=last_location.y, z=last_location.z)
        #     elif yaw >= 45:  #90    
        #         next_location = carla.Location(x=last_location.x, y=last_location.y - i*gap, z=last_location.z)
        #     else:            #0
        #         next_location = carla.Location(x=last_location.x + i*gap, y=last_location.y, z=last_location.z)
        #     print(next_location)
        #     print()
        #     next_waypoint = current_map.get_waypoint(next_location)
        #     last_location = next_waypoint.transform.location

        #     if next_waypoint.road_id != road_id:
        #         break
            
        #     road.append(next_waypoint)
        #     i += 1
        road = [current_waypoint]

        gap = 3

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


    def log_xml(self, args, config, vehicles, waypoints):
        parser = EXParser(args.fitness_path.replace('fitness.csv','log.xml'))
        new_setting = parser.addSetting('1', config.town, args.agent_mode)
        parser.addWeather(new_setting, config.weather)
        print(waypoints)
        print(vehicles)
        for i, vehicle in enumerate(vehicles):
            print(i,vehicle)
            if i-1 <= len(waypoints):
                parser.addVehicle(new_setting,str(vehicle.id),vehicle.type_id,waypoints[i])
            else:
                parser.addVehicle(new_setting,str(vehicle.id),vehicle.type_id,[])
        parser.update()



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
     
        # mod = 0
        # if mod == 0: #Straight
        #     for i in range(60):
        #         new_location = carla.Location(start_location.x + i*1, start_location.y, start_location.z)
        #         waypoints.append(new_location)
        # if mod == 1: #Wave
        #     for i in range(60):
        #         new_location = carla.Location(start_location.x + i*1, start_location.y-(abs(i%14-7))*0.3, start_location.z)
        #         waypoints.append(new_location)		
        # if mod == 2: #Collision
        #     for i in range(60):
        #         new_location = carla.Location(start_location.x + i*1, start_location.y+((abs(i%60-30)-30)/30)*5, start_location.z)
        #         waypoints.append(new_location)
        # if mod == 3: #TurnLeft
        #     for i in range(60):
        #         if i <= 15:
        #             new_location = carla.Location(waypoints[-1].x + 1, waypoints[-1].y, waypoints[-1].z)
        #         elif i <= 20:
        #             new_location = carla.Location(waypoints[-1].x + 1, waypoints[-1].y - 1, waypoints[-1].z)
        #         else:
        #             new_location = carla.Location(waypoints[-1].x, waypoints[-1].y - 1, waypoints[-1].z)
        #         waypoints.append(new_location)
        # if mod == 4: #TurnLeft-LessPoint
        #         waypoints.append(carla.Location(waypoints[-1].x + 5, waypoints[-1].y, waypoints[-1].z))
        #         waypoints.append(carla.Location(waypoints[-1].x + 5, waypoints[-1].y, waypoints[-1].z))
        #         waypoints.append(carla.Location(waypoints[-1].x + 5, waypoints[-1].y, waypoints[-1].z))
        #         waypoints.append(carla.Location(waypoints[-1].x + 5, waypoints[-1].y, waypoints[-1].z))
        #         waypoints.append(carla.Location(waypoints[-1].x, waypoints[-1].y-5, waypoints[-1].z))
        #         waypoints.append(carla.Location(waypoints[-1].x, waypoints[-1].y-5, waypoints[-1].z))
        #         waypoints.append(carla.Location(waypoints[-1].x, waypoints[-1].y-5, waypoints[-1].z))
        #         waypoints.append(carla.Location(waypoints[-1].x, waypoints[-1].y-5, waypoints[-1].z))

        return waypoints

    def run(self, args):
        """
        Run the challenge mode
        """
        # agent_class_name = getattr(self.module_agent, 'get_entry_point')()
        # self.agent_instance = getattr(self.module_agent, agent_class_name)(args.agent_config)
        
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
            # config get here just include the settings from the xml
            # currently xml doesot include vehicle and other information
            # thus config doesnot include them

            # run
            self._load_and_run_scenario(args, config)

            # for obj in gc.get_objects():
            #     try:
            #         if torch.is_tensor(obj) or (hasattr(obj, 'data') and torch.is_tensor(obj.data)):
            #             print(type(obj), obj.size())
            #     except:
            #         pass

            route_indexer.save_state(args.checkpoint)

        # save global statistics
        print("\033[1m> Registering the global statistics\033[0m")
        global_stats_record = self.statistics_manager.compute_global_statistics(route_indexer.total)
        StatisticsManager.save_global_record(global_stats_record, self.sensor_icons, route_indexer.total, args.checkpoint)


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
    parser.add_argument('--timeout', default="200.0",
                        help='Set the CARLA client timeout value in seconds')

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
    statistics_manager = StatisticsManager()

    try:
        print("begin")
        leaderboard_evaluator = LeaderboardEvaluator(arguments, statistics_manager)
        leaderboard_evaluator.run(arguments)

    except Exception as e:
        traceback.print_exc()
    finally:
        del leaderboard_evaluator


if __name__ == '__main__':
    main()

#!/bin/bash
export CARLA_ROOT=~/Projects/carla
export CARLA_SERVER=${CARLA_ROOT}/CarlaUE4.sh
export PYTHONPATH=$PYTHONPATH:${CARLA_ROOT}/PythonAPI
export PYTHONPATH=$PYTHONPATH:${CARLA_ROOT}/PythonAPI/carla
export PYTHONPATH=$PYTHONPATH:$CARLA_ROOT/PythonAPI/carla/dist/carla-0.9.10-py3.7-linux-x86_64.egg
export PYTHONPATH=$PYTHONPATH:leaderboard
export PYTHONPATH=$PYTHONPATH:leaderboard/team_code
export PYTHONPATH=$PYTHONPATH:scenario_runner

export LEADERBOARD_ROOT=leaderboard
export CHALLENGE_TRACK_CODENAME=SENSORS
export PORT=2000
export TM_PORT=8000
export DEBUG_CHALLENGE=0
export REPETITIONS=1 # multiple evaluation runs
export RESUME=True
export AGENT_MODE=0

export DATA_COLLECTION=True
export SAVE_IMG=False

export ROUTE_FILE=routes_short
current_time=$(date "+%Y-%m-%d|%H:%M:%S")

# Roach data collection
# export ROUTES=leaderboard/data/TCP_training_routes/${ROUTE_FILE}.xml
# export TEAM_AGENT=team_code/roach_ap_agent.py
# export TEAM_CONFIG=roach/config/config_agent.yaml
# export CHECKPOINT_ENDPOINT=data_collect_${ROUTE_FILE}_${current_time}.json
# export SCENARIOS=leaderboard/data/scenarios/all_towns_traffic_scenarios.json
# export SAVE_PATH=data/${ROUTE_FILE}_${current_time}/


# TCP evaluation
# export ROUTES=leaderboard/data/evaluation_routes/routes_lav_valid.xml
export ROUTES=leaderboard/data/TCP_training_routes/${ROUTE_FILE}.xml
export TEAM_AGENT=team_code/tcp_agent.py
export TEAM_CONFIG=TCP/epoch=59-last.ckpt
# export CHECKPOINT_ENDPOINT=results_TCP.json
export CHECKPOINT_ENDPOINT=data_collect_${ROUTE_FILE}_${current_time}.json
export SCENARIOS=leaderboard/data/scenarios/all_towns_traffic_scenarios.json
# export SAVE_PATH=data/results_TCP/
export SAVE_PATH=data/${ROUTE_FILE}_${current_time}/


# export RECORD_PATH=./

# python3 ${LEADERBOARD_ROOT}/leaderboard/leaderboard_evaluator.py \
# --scenarios=${SCENARIOS}  \
# --routes=${ROUTES} \
# --repetitions=${REPETITIONS} \
# --track=${CHALLENGE_TRACK_CODENAME} \
# --checkpoint=${CHECKPOINT_ENDPOINT} \
# --agent=${TEAM_AGENT} \
# --agent-config=${TEAM_CONFIG} \
# --debug=${DEBUG_CHALLENGE} \
# --record=${RECORD_PATH} \
# --resume=${RESUME} \
# --port=${PORT} \
# --fitness_path=${SAVE_PATH}/fitness.csv \
# --agent_mode=${AGENT_MODE} \
# --trafficManagerPort=${TM_PORT}


python3 ${LEADERBOARD_ROOT}/leaderboard/run_one_case.py \
--scenarios=${SCENARIOS}  \
--routes=${ROUTES} \
--repetitions=${REPETITIONS} \
--track=${CHALLENGE_TRACK_CODENAME} \
--checkpoint=${CHECKPOINT_ENDPOINT} \
--agent=${TEAM_AGENT} \
--agent-config=${TEAM_CONFIG} \
--debug=${DEBUG_CHALLENGE} \
--record=${RECORD_PATH} \
--resume=${RESUME} \
--port=${PORT} \
--fitness_path=${SAVE_PATH}/fitness.csv \
--agent_mode=${AGENT_MODE} \
--trafficManagerPort=${TM_PORT}



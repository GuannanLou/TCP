
import os
import sys
import traceback
import numpy as np

from SBT.problem import CustomizedProblem, SurrogateProblem
from utils.utils import mkdir, savepath_parser

from pymoo.core.problem import ElementwiseProblem
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.rnd import FloatRandomSampling
from pymoo.termination import get_termination
from pymoo.optimize import minimize


GA = os.environ['GA']==True
log = os.environ['LOG']==True
surrogate = os.environ['SURROGATE']==True
save_surrogate_log = True
# print("GA  | log  |surrogate")
# print("{}| {}  |  {}".format(GA, log, surrogate))

# surrogate_scenario = None
# surrogate_scenario = 'surrogate/routes_short_2023-06-13|18:27:28/' 
# 'surrogate/routes_short_2023-05-31|15:47:49/scenario.csv'
# surrogate_scenario = 'data/routes_short_2023-06-16|07:52:55/'



def random_search(arguments, leaderboard_evaluator, route_indexer, case_number=3000, scenario_vecs=None):
    print("begin")
    arguments.log=log
    config = None
    while route_indexer.peek():
        config = route_indexer.next()
    config.original_trajectory = [config.trajectory[0], config.trajectory[1]]

    if scenario_vecs==[]:
        scenario_vecs = np.random.rand(case_number, 9+3+2)
    for scenario_vec in scenario_vecs:
        leaderboard_evaluator.run_one_case(scenario_vec, config)



def GA_search(arguments, leaderboard_evaluator, route_indexer, pop_size = 50, n_offsprings = 10, generations = 76):
    print("begin")
    arguments.log=log
    config = None
    while route_indexer.peek():
        config = route_indexer.next()
    config.original_trajectory = [config.trajectory[0], config.trajectory[1]]

    savepath = savepath_parser(arguments.fitness_path)
    print(savepath)

    mkdir(savepath)
    if save_surrogate_log:
        output_file = savepath+'/console.log'
        sys.stdout = open(output_file, 'w')

    problem = None
    if surrogate:
        print('surrogate')
        problem = SurrogateProblem(config, surrogate_path='./data/'+arguments.fitness_path.split('/')[1]+'/')
    else:
        problem = CustomizedProblem(arguments.fitness_path,
                                    arguments.fitness_path.replace('fitness.csv','criterion.csv'),
                                    leaderboard_evaluator.run_one_case,
                                    config)
    algorithm = NSGA2(
        pop_size=pop_size,
        n_offsprings=n_offsprings,
        sampling=FloatRandomSampling(),
        crossover=SBX(prob=0.9, eta=15),
        mutation=PM(eta=20),
        eliminate_duplicates=True
    )
    termination = get_termination("n_gen", generations)

    res = minimize(problem,
        algorithm,
        termination,
        seed=1,
        save_history=False,
        verbose=True)

    X = res.X
    F = res.F
    
    print(X)
    print(F)

    np.savez('./data/'+arguments.fitness_path.split('/')[1]+'/output.npz', X, F)


    if save_surrogate_log:
        sys.stdout.close()
        sys.stdout = sys.__stdout__


def search_based_testing(arguments, leaderboard_evaluator, route_indexer):
    try:
        if GA: 
            GA_search(arguments, 
                    leaderboard_evaluator, 
                    route_indexer, 
                    pop_size     = 50, 
                    n_offsprings = 10, 
                    generations  = 76)
        else:
            scenario_vecs = np.random.rand(200,14)
            scenario_vecs = np.array([
                                [0.59777615, 0.72223506, 0.12374883, 0.30677363, 0.70592351,
                                    0.80276719, 0.79815411, 0.84394874, 0.01639043, 0.74687154,
                                    0.54046098, 0.68564598, 0.21594995, 0.10060122],
                                [0.39718882, 0.08087786, 0.37792418, 0.07962608, 0.98978885,
                                    0.85734188, 0.9852377 , 0.87401614, 0.68767837, 0.52781966,
                                    0.49976442, 0.681722  , 0.34517205, 0.95409391],
                                # [0.34050009, 0.7266114 , 0.65643148, 0.52285901, 0.90019775,
                                #     0.77005633, 0.98038164, 0.8759482 , 0.40710583, 0.45522409,
                                #     0.81124623, 0.92224074, 0.19620584, 0.84855626],
                                # [0.91695837, 0.72107255, 0.3723671 , 0.28326   , 0.76679765,
                                #     0.00376423, 0.96084713, 0.86335041, 0.76311738, 0.72054708,
                                #     0.51065933, 0.69450803, 0.21293915, 0.76181747],
                                # [0.35233707, 0.75028226, 0.10965908, 0.87967191, 0.70753986,
                                #     0.84855801, 0.98681216, 0.88160435, 0.39627272, 0.76438451,
                                #     0.69382362, 0.7107312 , 0.238529  , 0.97225962]
                            ])
            print(scenario_vecs)

            random_search(arguments, 
                        leaderboard_evaluator, 
                        route_indexer, 
                        case_number=3000, 
                        scenario_vecs=scenario_vecs)
    except Exception as e:
        traceback.print_exc()
    finally:
        if not surrogate:
            del leaderboard_evaluator
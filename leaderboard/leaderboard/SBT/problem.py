import sys
import numpy as np
import joblib

from pymoo.core.problem import ElementwiseProblem
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.rnd import FloatRandomSampling
from pymoo.termination import get_termination
from pymoo.optimize import minimize

class CustomizedProblem(ElementwiseProblem):
    def __init__(self, fitness_file, cirtion_file, fitness_generator, config):
        super().__init__(n_var=14,
                         n_obj=3,
                         xl=np.zeros(14),
                         xu=np.ones(14))
        self.fitness_file = fitness_file
        self.cirtion_file = cirtion_file
        self.fitness_generator = fitness_generator
        self.config = config


    def _evaluate(self, x, out, *args, **kwargs):
        dict_cirtion_index = {"RouteCompletionTest": 0,   
            "RouteCompletionTest_figure": 1,
            "OutsideRouteLanesTest": 2, 
            "OutsideRouteLanesTest_figure": 3,
            "CollisionTest": 4,         
            "CollisionTest_figure": 5,
            "RunningRedLightTest": 6,   
            "RunningRedLightTest_figure": 7,
            "RunningStopTest": 8,       
            "RunningStopTest_figure": 9,
            "InRouteTest": 10, 
            "InRouteTest_figure": 11,          
            "AgentBlockedTest": 12,
            "AgentBlockedTest_figure": 13,      
            "Timeout": 14}
        
        # x[6] = 0

        self.fitness_generator(x, self.config)
        result = {
            'RouteCompletionTest':0,
            'OutsideRouteLanesTest':0,
            'CollisionTest':0,
            'RunningRedLightTest':0,
            'RunningStopTest':0,
            'InRouteTest':0,
            'AgentBlockedTest':0,
            'Timeout':0
        }
        with open(self.cirtion_file, 'r') as file:
            data = [float(item) for item in file.readlines()[-1].strip().split(',')]
            result['RouteCompletionTest']   = data[dict_cirtion_index["RouteCompletionTest_figure"]]/100
            result['OutsideRouteLanesTest'] = 1-data[dict_cirtion_index["OutsideRouteLanesTest_figure"]]/100
            result['CollisionTest']         = data[dict_cirtion_index["CollisionTest"]]
            result['RunningRedLightTest']   = 1-data[dict_cirtion_index["RunningRedLightTest"]]
            result['RunningStopTest']       = 1-data[dict_cirtion_index["RunningStopTest"]]
            result['InRouteTest']           = 1-data[dict_cirtion_index["InRouteTest"]]
            result['AgentBlockedTest']      = 1-data[dict_cirtion_index["AgentBlockedTest"]]
            result['Timeout']               = 1-data[dict_cirtion_index["Timeout"]]

        with open(self.fitness_file, 'r') as file:
            data = [float(item) for item in file.readlines()[-1].strip().split(',')] 
            result['CollisionTest'] = 0 if result['CollisionTest'] == 1 else min(data[1],2)/2
            
        out['F'] = [
            result['RouteCompletionTest'],
            result['OutsideRouteLanesTest'],
            result['CollisionTest']
        ]


class SurrogateProblem(ElementwiseProblem):
    def __init__(self, config, surrogate_path):
        super().__init__(n_var=14,
                         n_obj=3,
                         xl=np.zeros(14),
                         xu=np.ones(14))
        self.config = config
        self.surrogate_path = surrogate_path
        

    def _evaluate(self, x, out, *args, **kwargs):
        # model_path = './tools/models/'
        model_path = './tools/models/regression-Kriging'
        surrogate_models = {"RouteCompletionTest"  : joblib.load(model_path+'-RouteCompletionTest.pkl'), 
                            "CollisionTest"        : joblib.load(model_path+'-CollisionTest.pkl'), 
                            "OutsideRouteLanesTest": joblib.load(model_path+'-OutsideRouteLanesTest.pkl'), 
                            "Timeout"              : joblib.load(model_path+'-Timeout.pkl')}
        # result = [
        #     1-surrogate_models["OutsideRouteLanesTest"].predict([x])[0],
        #     1-surrogate_models["CollisionTest"].predict([x])[0],
        #     1-surrogate_models["Timeout"].predict([x])[0]
        # ]
        result = np.array([
            surrogate_models["OutsideRouteLanesTest"].predict([x])[0],
            surrogate_models["CollisionTest"].predict([x])[0],
            surrogate_models["RouteCompletionTest"].predict([x])[0]
        ])
        result[result>1] = 1
        result[result<0] = 0
        # print(result)

        file = open(self.surrogate_path+'criterion.csv', 'a')
        file.write(','.join([str(item) for item in result])+'\n')
        file.close()

        file = open(self.surrogate_path+'scenario.csv', 'a')
        file.write(','.join([str(item) for item in x])+'\n')
        file.close()

        out['F'] = result


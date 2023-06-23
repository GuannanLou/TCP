# -*- coding: utf-8 -*-

import numpy
import numpy as np
from sklearn import preprocessing
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF


class Kriging:

    def __init__(self, data=None, label=None, cluster = None):
            self.create_model_from_cluster(data, label)

    def create_model_from_cluster(self, data, label):
        self.scaler = preprocessing.StandardScaler()
        X = np.array(data)
        y = np.array(label)

        y[y < 0] = 0
        y[y > 1] = 1

        X = self.scaler.fit_transform(X)
        kernel = 1.0 * RBF(1.0)  # squared-exponential kernel
        self.model = GaussianProcessRegressor(kernel=kernel, random_state=0).fit(X, y)
    
    def predict(self, value):
        value = numpy.array(value)
        B=self.scaler.transform(value)
        y_pred = self.model.predict(B)

        return  y_pred



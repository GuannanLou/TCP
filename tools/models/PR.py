# -*- coding: utf-8 -*-
"""
"""

import numpy
import numpy as np
import pandas as pd
from sklearn import preprocessing


class Polynomial_Regression:

    def __init__(self, degree=-1, data=None, label=None):
        self.create_model_from_cluster(data, label, degree)

    def create_model_from_cluster(self, data, label, deg):
        # X = data
        # y = label

        self.scaler = preprocessing.StandardScaler()

        X = np.array(data) # features from 0 to 15th index
        y = np.array(label) # value at 16th index
        y[y < 0] = 0
        y[y > 1] = 1

        X = self.scaler.fit_transform(X)
        from sklearn.linear_model import LinearRegression
        from sklearn.preprocessing import PolynomialFeatures
        self.poly_reg = PolynomialFeatures(degree=deg)
        X_poly = self.poly_reg.fit_transform(X)
        self.pol_reg = LinearRegression()
        self.pol_reg.fit(X_poly, y)

    def predict(self, value):
        value = numpy.array(value)
        B = self.scaler.transform(value)
        y_pred = self.pol_reg.predict(self.poly_reg.fit_transform(B))
        return y_pred



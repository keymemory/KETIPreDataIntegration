# -*- coding: utf-8 -*-
import os
import pandas as pd
import numpy as np
from sklearn.impute import KNNImputer

import torch
import torch.nn as nn

from models.model import RecurrentAutoencoder
from models.train_model import train_model, get_representation


class Alignment():
    def __init__(self, config, x1, x2):
        """
        Initialize Alignment class and prepare OverlapData based on min-max index.

        :param config: config 
        :type config: dictionary

        :param x1: the first dataframe
        :type x1: dataFrame

        :param x1: the second dataframe
        :type x2: dataFrame

        example
            >>> config ={
                    "model": 'RNN_AE', # one of ['down', 'up', 'RNN_AE']
                    "parameter": {
                        "emb_dim" : 32, 
                        "batch": 128, # int [0 ~ 256]  
                        "epoch": 50, # int [0 ~ 300] 수렴여부를 train 을 통해 확인 후 최적화
                        "mode" : 'train'
                        }
                    }
            >>> data_alilgnment = Alignment(config, x1, x2)
            >>> data_alilgnment.getResult()
        """
        
        self.config = config 
        self.x1 = x1
        self.x2 = x2
        
        self.data_concat = self.getOverlapData(x1, x2) 
   
    def getOverlapData(self, x1, x2):
        """
        수집 주기 및 수집 시간이 다른 두 시계열 데이터의 공통 수집 시간에 해당하는 데이터를 통합하는 함수
        시간 index는 수집 주기가 짧은 데이터를 기준으로 정렬됨
        수집 주기가 긴 데이터의 missing value는 np.nan 값으로 표기됨

        :param x1: the first dataframe input
        :type x1: dataFrame

        :param x1: the second dataframe input
        :type x2: dataFrame

        :return: overlap Data with [maximum MinIndex : minimum MaxIndex]
        :rtype: dataFrame
        """

        # check each min-max range
        v1_min = x1.index.min()
        v2_min = x2.index.min()
        v1_max = x1.index.max()
        v2_max = x2.index.max()
        
        v_min = np.max([v1_min, v2_min])
        v_max = np.min([v1_max, v2_max])
        
        # 공통 수집 시간에 해당하는 통합 데이터 도출
        data_concat = pd.concat([x1, x2], axis=1, join='outer')
        data_concat = data_concat.iloc[v_min:v_max + 1, :]
        return data_concat
    
    def getResult(self) :
        """
        선택된 모델을 사용하여 align 된 데이터를 dataFrame 형태로 반환하는 함수

        :return: concat & aligned dataset
        :rtype: dataFrame
        :colunm names(model=='up' or 'down'): ["data1_col1", "data1_col2", "data1_col3", "data2_col1", "data2_col2", "data2_col3"]
        :column names(model=='RNN_AE'): ["concat_emb1", "concat_emb2", ..., "concat_emb32"]
        """
        model = self.config['model']
        parameter = self.config['parameter']

        if model == 'up':
            result = self.upsampling(self.data_concat, parameter)
        elif model == 'down':
            result = self.downsampling(self.data_concat)
        elif model == 'RNN_AE' :
            result = self.RNN_AE(self.data_concat, parameter)     
        else :
            print('Not Available')
        return result
        
    def upsampling(self, data_concat, parameter):
        """
        선택된 모델을 사용하여 upsampling 된 데이터를 dataFrame 형태로 반환하는 함수
        
        :param data_concat: overlap Data with [maximum MinIndex : minimum MaxIndex]
        :type data_concat: dataFrame
        
        :param parameter: config for upsampling method
        :type parameter: dictionary
        
        :return: concat & aligned dataset
        :rtype: dataFrame
        :shape: [x1과 x2의 공통 수집 기간 중 주기가 짧은 데이터의 시간 index 개수, 변수 개수]
        """
        method = parameter["method"]
        n_neighbors = parameter["n_neighbors"]

        if method == 'knn':
            imputer = KNNImputer(n_neighbors=n_neighbors)
            data_concat_transform = imputer.fit_transform(data_concat)
            data_concat_transform = pd.DataFrame(data_concat_transform, columns=data_concat.columns)
        elif method == 'mean':
            data_concat_transform = data_concat.fillna(data_concat.mean())
        else:
            print('not imp')
        return data_concat_transform
    
    def downsampling(self, data_concat):
        """
        downsampling 된 데이터를 dataFrame 형태로 반환하는 함수
        
        :param data_concat: overlap Data with [maximum MinIndex : minimum MaxIndex]
        :type data_concat: dataFrame

        :return : concat & aligned dataset
        :rtype: dataFrame
        :shape: [x1과 x2의 공통 수집 기간 중 주기가 긴 데이터의 시간 index 개수, 변수 개수]
        """
        data_concat_imputed = data_concat.dropna(axis=0)
        return data_concat_imputed
    
    def RNN_AE(self, data_concat, parameter):
        """
        RAE 모델을 기반으로 새롭게 도출된 변수로 align 된 데이터를 dataFrame 형태로 반환하는 함수

        :param data_concat: overlap Data with [maximum MinIndex : minimum MaxIndex]
        :type data_concat: dataFrame
        
        :param parameter: config for RNN_AE model
        :type parameter: dictionary
        
        :return : concat & aligned dataset
        :rtype: dataFrame
        :shape: [x1과 x2의 공통 수집 기간 중 주기가 짧은 데이터의 시간 index 개수 - window_size, emb_dim]
        """
        n_features = len(data_concat.columns)

        # NaN 값을 0으로 대치한 데이터를 사용하여 dataloader 구축
        data_concat = data_concat.fillna(0)
        train_loader, inference_loader = self.get_loaders(data=data_concat, window_size=parameter['window_size'], batch_size=parameter['batch_size'])
        
        # 모델 학습
        model = RecurrentAutoencoder(n_features=n_features, embedding_dim=parameter['emb_dim'])
        model, history = train_model(model, train_loader, parameter)

        # 학습된 모델 저장
        os.makedirs('./checkpoints', exist_ok=True)
        torch.save(model.state_dict(), './checkpoints/best_model.pt')
        
        # 학습된 모델로 각 time window에 대한 새로운 변수 추출
        output = get_representation(model, inference_loader, parameter)
        
        # 도출된 결과물을 dataFrame 형태로 변환
        data_col = [ f'concat_emb{i}' for i in range(1, parameter['emb_dim'] + 1)]
        data_index = self.data_concat.index[parameter['window_size']:]
        output = pd.DataFrame(output, columns=data_col, index=data_index)
        return output
     
    def get_loaders(self, data, window_size, batch_size):
        """
        전체 시계열 데이터를 기반으로 window_size 크기의 time window를 생성하고 이에 대한 dataloader를 구축하는 함수
        
        :param data: overlap Data with [maximum MinIndex : minimum MaxIndex]
        :type data: dataFrame
        
        :param window_size: input length
        :type window_size: int
        
        :param batch_size: batch size
        :type batch_size: int
        
        :return: dataloaders for training and inference
        :rtype: DataLoader
        
        """

        # numpy array 형태로 변환
        data = data.values

        # 전체 시계열 데이터를 기반으로 한 시점씩 슬라이딩하면서 window_size 크기의 time window 생성
        windows = []
        for i in range(len(data) - window_size):
            window = data[i:i + window_size, :]
            windows.append(window)

        # 분할된 time window 단위의 데이터를 tensor 형태로 변경하여 데이터셋 및 데이터로더 구축
        dataset = torch.utils.data.TensorDataset(torch.Tensor(np.array(windows)))
        train_loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)
        inference_loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=False)
        return train_loader, inference_loader
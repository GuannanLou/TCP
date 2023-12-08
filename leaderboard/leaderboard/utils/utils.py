import os

def mkdir(path):
    # print(path)
    folder = os.path.exists(path)
    if not folder:
        os.makedirs(path)
    
def savepath_parser(save_path):
    return './data/'+save_path.split('/')[1]
import tensorflow.compat.v1 as tf  
tf.disable_v2_behavior()
import numpy as np
flags = tf.app.flags
FLAGS = flags.FLAGS


flags.DEFINE_integer('hidden3', 32, 'Number of units in hidden layer 3.')
flags.DEFINE_integer('discriminator_out', 1, 'discriminator_out.')
flags.DEFINE_float('discriminator_learning_rate', 0.0001, 'Initial learning rate.')
flags.DEFINE_float('learning_rate', 0.01, 'Initial learning rate.')
flags.DEFINE_integer('hidden1', 32, 'Number of units in hidden layer 1.')
flags.DEFINE_integer('hidden2', 32, 'Number of units in hidden layer 2.')
flags.DEFINE_float('weight_decay', 0.005, 'Weight for L2 loss on embedding matrix.')
flags.DEFINE_float('dropout', 0.005, 'Dropout rate (1 - keep probability).')
flags.DEFINE_integer('features', 1, 'Whether to use features (1) or not (0).')
flags.DEFINE_integer('seed', 50, 'seed for fixing the results.')
flags.DEFINE_integer('iterations',75, 'number of iterations.')
flags.DEFINE_integer('nb_node_samples',300, 'number of node sampled from the network.')



'''
infor: number of clusters 
'''
infor = {'cora': 7, 'citeseer': 6, 'pubmed':3}


'''
We did not set any seed when we conducted the experiments described in the paper;
We set a seed here to steadily reveal better performance of ARGA
'''
seed = 7
np.random.seed(seed)
tf.set_random_seed(seed)

def get_settings(dataname, model, task,nb_node_samples, measure):
    if dataname != 'citeseer' and dataname != 'cora' and dataname != 'pubmed':
        print('error: wrong data set name')
    if model != 'arga_ae' and model != 'arga_vae':
        print('error: wrong model name')
    if task != 'clustering' and task != 'link_prediction':
        print('error: wrong task name')

    if task == 'clustering':
        iterations = FLAGS.iterations
        clustering_num = infor[dataname]
        re = {'data_name': dataname, 'iterations' : iterations, 'clustering_num' :clustering_num, 'model' : model}
    elif task == 'link_prediction':
        iterations = 4 * FLAGS.iterations
        re = {'data_name': dataname, 'iterations' : iterations,'model' : model, 'nb_node_samples': nb_node_samples, 'measure':measure}

    return re


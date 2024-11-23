from evaluation import get_roc_score, clustering_latent_space,sigmoid
from input_data import load_data, load_label
from model import * # GCNModelAE, GCNModelVAE,GravityGCNModelAE,GravityGCNModelVAE,DeepGCNModelAE,DeepGCNModelVAE,
from optimizer import OptimizerAE, OptimizerVAE
from preprocessing import *
from sampling import get_distribution, node_sampling,top_nodes_sampling,node_sparse_sampling,node_sampling_with_rejection,mcmc_node_sampling,node_uniform_sampling ,node_sampling_gn

from network_property import fit_power_law, subgraph_property
import numpy as np
import os
import scipy.sparse as sp
#import tensorflow as tf
import tensorflow.compat.v1 as tf  # 由于使用了placeholder，所以使用该句及下句语句
tf.disable_v2_behavior()
import networkx as nx
import time

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['CUDA_VISIBLE_DEVICES'] = "0,1"
#tf.compat.v1.logging.set_verbosity(tf.compat.v1.logging.ERROR)
# Set TensorFlow to use only one CPU core
tf.config.threading.set_inter_op_parallelism_threads(1)
tf.config.threading.set_intra_op_parallelism_threads(1)

flags = tf.compat.v1.app.flags
FLAGS = flags.FLAGS

gpus = tf.config.experimental.list_physical_devices('GPU')

# Select graph dataset
flags.DEFINE_string('dataset', 'cora', 'Name of the graph dataset')
''' Available datasets:

- cora: Cora scientific publications citation network, from LINQS
- citeseer: Citeseer scientific publications citation network, from LINQS
- pubmed: PubMed scientific publications citation network, from LINQS
- google: Google.com hyperlinks network, from SNAP
Due to size constraints, the three additional graphs used in the paper
(SBM, Patent and Youtube) will be added via external links. We refer to
section 4 of the paper for more information about datasets.
'''

# Select machine learning task to perform on graph
flags.DEFINE_string('task', 'link_prediction', 'Name of the learning task,- link_prediction: Link Prediction, - node_clustering: Node Clustering')


# Model
flags.DEFINE_string('model', 'gcn_vae', 'Name of the model')
''' Available Models:
- gcn_ae: Graph Autoencoder with 2-layer GCN encoder and inner product decoder
- gcn_vae: Graph Variational Autoencoder with Gaussian priors, 2-layer GCN
           encoders for mu and sigma, and inner product decoder
'''
# Model parameters
flags.DEFINE_float('dropout', 0., 'Dropout rate (1 - keep probability)')
#cora=0.2
flags.DEFINE_integer('iterations', 200, 'Number of iterations in training')
flags.DEFINE_boolean('features', True, 'Include node features or not in encoder')
flags.DEFINE_float('learning_rate', 0.001, 'Initial learning rate (with Adam)')#0.001
# cora, citeseer, pubmed 0.01
# protein 0.05
# cath cmat enron 0.005
flags.DEFINE_integer('hidden', 32, 'Number of units in GCN hidden layer')
flags.DEFINE_integer('dimension', 16, 'Dimension of encoder output, i.e.  embedding dimension')
#32  16#
# Model of GAE parameters
# flags.DEFINE_boolean('fastgae', True, 'Whether to use the FastGAE framework')

flags.DEFINE_string('fastgae', 'sn', 'Whether to use the FastGAE framework')
flags.DEFINE_integer('nb_node_samples', 300, 'Number of nodes to sample at each iteration, i.e. sampled subgraph size')
flags.DEFINE_integer('node_start', 0, 'Number of index nodes to sample at the beginning')
flags.DEFINE_string('measure', 'core', 'Node importance measure used in sampling: degree, core or uniform')
flags.DEFINE_float('alpha', 1.0, 'alpha hyperparameter of p_i distribution')
flags.DEFINE_boolean('replace', False, 'Whether to sample nodes with (True)  or without (False) replacement')
flags.DEFINE_boolean('normalize', False, 'Whether to normalize embedding  vectors of gravity models')
flags.DEFINE_float('epsilon', 0.01, 'Add epsilon to distances computations in gravity models, for numerical stability')
# Experimental setup parameters
flags.DEFINE_integer('nb_run', 1, 'Number of model run + test')
flags.DEFINE_float('prop_val', 5., 'Proportion of edges in validation set  (for Link Prediction task)')
#prop_val = 5.0  prop_test = 10.
flags.DEFINE_float('prop_test', 10., 'Proportion of edges in test set (for Link Prediction task)')
flags.DEFINE_float('lamb', 1., 'lambda parameter from Gravity AE/VAE models  as introduced in section 3.5 of paper, to \
                                balance mass and proximity terms')
flags.DEFINE_boolean('validation', False, 'Whether to report validation results at each iteration (for Link Prediction task)')
flags.DEFINE_boolean('verbose', True, 'Whether to print comments details')
flags.DEFINE_integer('num_layers',3, 'Sampled by gn layers')
flags.DEFINE_integer('times', 1, 'repeated for demonstrating small world property')


# Lists to average final results
if FLAGS.fastgae:
    mean_time_proba_computation = []
if FLAGS.task == 'link_prediction':
    mean_roc = []
    mean_ap = []
elif FLAGS.task == 'node_clustering':
    mean_mutual_info = []
else:
    raise ValueError('Undefined task!')




# Load graph dataset
if FLAGS.verbose:
    print("Loading data...")
adj_init, features_init = load_data(FLAGS.dataset)

# Load ground-truth labels for node clustering task
if FLAGS.task == 'node_clustering':
    labels = load_label(FLAGS.dataset)


print("\n Datasets ", FLAGS.dataset,
              "Nodes", FLAGS.nb_node_samples, "on", FLAGS.task, "\n",
              "___________________________________________________\n")


# The entire training+test process is repeated FLAGS.nb_run times
mean_time = []
mean_similar = []
mean_error = []
mean_centrality = []
sparse_array = []


auc = []
ap = []
times = []
similarity = []


for _ in range(1):
    for i in range(FLAGS.nb_run):

        # Preprocessing and initialization steps
        if FLAGS.verbose:
            print("Preprocessing data...")

        # Edge Masking for Link Prediction:
        if FLAGS.task == 'link_prediction' :
            # Compute Train/Validation/Test sets
            adj, val_edges, val_edges_false, test_edges, test_edges_false = mask_test_edges(adj_init, FLAGS.prop_test, FLAGS.prop_val)
            G = nx.from_scipy_sparse_array(adj)
            # G = nx.from_numpy_array(adj.toarray())
        else:
            adj = adj_init
            G = nx.from_scipy_sparse_array(adj)
            # G = nx.from_numpy_array(adj.toarray())

        # Compute number of nodes


        num_nodes = adj.shape[0]

        # Preprocessing on node features
        if FLAGS.features:
            features = features_init
        else:
            # If features are not used, replace feature matrix by identity matrix
            features = sp.identity(num_nodes)
        features = sparse_to_tuple(features)
        num_features = features[2][1]
        features_nonzero = features[1].shape[0]

        # Start computation of running times
        t_start = time.time()

        # FastGAE: node sampling for stochastic subgraph decoding
        if FLAGS.fastgae =='sn':
            #Node sampling by centrality probability
            if FLAGS.verbose:
                print("Computing p_i distribution for", FLAGS.measure, "sampling")
            t_proba = time.time()
            # Node-level p_i degree-based, core-based or uniform distribution
        
            node_distribution =  get_distribution(FLAGS.measure,FLAGS.dataset, FLAGS.alpha, adj)
            mean_time_proba_computation.append(time.time() - t_proba)   # Running time to compute distribution
            sampled_nodes, adj_label, adj_sampled_sparse = node_sampling(adj, node_distribution,FLAGS.nb_node_samples, FLAGS.replace)
            


        elif FLAGS.fastgae == 'tn':
            sampled_nodes, adj_label, adj_sampled_sparse = top_nodes_sampling(adj,FLAGS.dataset, FLAGS.measure, FLAGS.nb_node_samples)
        
        elif FLAGS.fastgae == 'un':
            sampled_nodes, adj_label, adj_sampled_sparse = node_uniform_sampling(adj, FLAGS.nb_node_samples, FLAGS.replace)
        
        elif FLAGS.fastgae =='rn':
            #Update sampled subgraph
            node_distribution =  get_distribution(FLAGS.measure,FLAGS.dataset, FLAGS.alpha, adj)
            sampled_nodes, adj_label, adj_sampled_sparse = node_sampling_with_rejection(adj, node_distribution, FLAGS.nb_node_samples, FLAGS.replace) 
        elif FLAGS.fastgae =='gn':
            node_distribution =  get_distribution(FLAGS.measure,FLAGS.dataset, FLAGS.alpha, adj)
            sampled_nodes, adj_label, adj_sampled_sparse = node_sampling_gn(adj, node_distribution, FLAGS.nb_node_samples, FLAGS.num_layers,FLAGS.replace) 

        elif FLAGS.fastgae =='mcmc':
            node_distribution =  get_distribution(FLAGS.measure,FLAGS.dataset, FLAGS.alpha, adj)
            sampled_nodes, adj_label, adj_sampled_sparse = mcmc_node_sampling(adj, node_distribution,FLAGS.nb_node_samples, FLAGS.replace)
            


        elif FLAGS.fastgae =='sparse':
            # node_sparse_sampling(adj,datasets,measure,num_sampled_nodes,node_start) ,average_centrality
            sampled_nodes, adj_label, adj_sampled_sparse,average_centrality = node_sparse_sampling(adj,FLAGS.dataset, FLAGS.measure, FLAGS.nb_node_samples,FLAGS.node_start)
            mean_centrality.append(average_centrality)
        else:
            sampled_nodes = np.array(range(FLAGS.nb_node_samples))

        sampled_nodes1, adj_label1, adj_sampled_sparse1 = top_nodes_sampling(adj,FLAGS.dataset, FLAGS.measure, FLAGS.nb_node_samples)
        


        # 保存采样的节点集合
        # common = np.intersect1d(sampled_nodes,sampled_nodes1)
        # print('Number of common elements',common.size/sampled_nodes.shape[0])
        # print('Test Done! ')

        # Define placeholders
        placeholders = {
            'features': tf.sparse_placeholder(tf.float32),
            'adj': tf.sparse_placeholder(tf.float32),
            'adj_orig': tf.sparse_placeholder(tf.float32),
            'dropout': tf.placeholder_with_default(0., shape = ()),
            'sampled_nodes': tf.placeholder_with_default(sampled_nodes, shape = [FLAGS.nb_node_samples])
        }

        # Create model
        if FLAGS.model == 'gcn_ae':
            # Graph Autoencoder
            model = GCNModelAE(placeholders, num_features, features_nonzero)
        elif FLAGS.model == 'gcn_vae':
            # Graph Variational Autoencoder
            model = GCNModelVAE(placeholders, num_features, num_nodes, features_nonzero)
        elif FLAGS.model == 'gcn_deepae':
            # Deep Graph Autoencoder
            model = DeepGCNModelAE(placeholders, num_features, features_nonzero)
        elif FLAGS.model == 'gcn_deepvae':
            # Deep Graph Variational Autoencoder
            model = DeepGCNModelVAE(placeholders, num_features, num_nodes, features_nonzero)
        else:
            raise ValueError('Undefined model!')


        # Optimizer
        strategies = ['sn','tn','sparse','rn','gn','mcmc']
        if FLAGS.fastgae in strategies:
            num_sampled = adj_sampled_sparse.shape[0]
            sum_sampled = adj_sampled_sparse.sum()
            if sum_sampled ==0:
                pos_weight = float(num_nodes * num_nodes - adj.sum()) / adj.sum()
            else:
                pos_weight = float(num_sampled * num_sampled - sum_sampled) / sum_sampled
            norm = num_sampled * num_sampled / float((num_sampled * num_sampled - sum_sampled) * 2)
        else:
            pos_weight = float(num_nodes * num_nodes - adj.sum()) / adj.sum()
            norm = num_nodes * num_nodes / float((num_nodes * num_nodes - adj.sum()) * 2)

        if FLAGS.model in ('gcn_ae', 'linear_ae', 'gcn_deepae','gravity_ae'):
            opt = OptimizerAE(preds = model.reconstructions,
                            labels = tf.reshape(tf.compat.v1.sparse_tensor_to_dense(placeholders['adj_orig'],
                                                                            validate_indices = False), [-1]),
                            pos_weight = pos_weight,
                            norm = norm)


        elif FLAGS.model in ('gcn_vae', 'linear_vae', 'deep_gcn_vae','gravity_vae'):
            opt = OptimizerVAE(preds = model.reconstructions,
                            labels = tf.reshape(tf.compat.v1.sparse_tensor_to_dense(placeholders['adj_orig'],
                                                                            validate_indices = False), [-1]),
                            model = model,
                            num_nodes = num_nodes,
                            pos_weight = pos_weight,
                            norm = norm)
        else:
            raise ValueError('Undefined model!')
        

        # Normalization and preprocessing on adjacency matrix
        adj_norm = preprocess_graph(adj)
        if not FLAGS.fastgae:
            adj_label = sparse_to_tuple(adj + sp.eye(num_nodes))

        # Initialize TF session
        sess = tf.compat.v1.Session()
        sess.run(tf.compat.v1.global_variables_initializer())
        similar = []
        # Model training
        if FLAGS.verbose:
            print("Training...")
        sparse_arr = []
        for iter in range(FLAGS.iterations):
            # Flag to compute running time for each iteration
            t = time.time()
            # Construct feed dictionary
            feed_dict = construct_feed_dict(adj_norm, adj_label, features, placeholders)

            if FLAGS.fastgae == 'sn':
                # Update sampled subgraph
                feed_dict.update({placeholders['sampled_nodes']: sampled_nodes})
                # New node sampling
                sampled_nodes, adj_label, _ =  node_sampling(adj, node_distribution,FLAGS.nb_node_samples, FLAGS.replace)#sampled_nodes, adj_label, adj_sampled_sparse

            elif FLAGS.fastgae == 'tn':
                # Update sampled subgraph
                feed_dict.update({placeholders['sampled_nodes']: sampled_nodes})
                # Node sampling but the nodes is stational
                sampled_nodes, adj_label, _ =  top_nodes_sampling(adj,FLAGS.dataset, FLAGS.measure, FLAGS.nb_node_samples)
            elif FLAGS.fastgae == 'un':
                feed_dict.update({placeholders['sampled_nodes']: sampled_nodes})
                sampled_nodes, adj_label, adj_sampled_sparse = sampled_nodes, adj_label, adj_sampled_sparse #node_uniform_sampling(adj, FLAGS.nb_node_samples, FLAGS.replace)

            elif FLAGS.fastgae =='mcmc':
                feed_dict.update({placeholders['sampled_nodes']: sampled_nodes})
                sampled_nodes, adj_label, adj_sampled_sparse = mcmc_node_sampling(adj, node_distribution,FLAGS.nb_node_samples, FLAGS.replace)
            elif FLAGS.fastgae =='gn':
                # node_distribution =  get_distribution(FLAGS.measure,FLAGS.dataset, FLAGS.alpha, adj)
                feed_dict.update({placeholders['sampled_nodes']: sampled_nodes})
                sampled_nodes, adj_label, adj_sampled_sparse = node_sampling_gn(adj, node_distribution, FLAGS.nb_node_samples, FLAGS.num_layers,FLAGS.replace) 


            elif FLAGS.fastgae =='rn':
                #Update sampled subgraph
                feed_dict.update({placeholders['sampled_nodes']:sampled_nodes})
                sampled_nodes, adj_label, _ = node_sampling_with_rejection(adj, node_distribution, FLAGS.nb_node_samples, FLAGS.replace) 


            elif FLAGS.fastgae =='sparse':
                # node_sparse_sampling(adj,datasets,measure,num_sampled_nodes,node_start)
                sampled_nodes, adj_label, _, _  = node_sparse_sampling(adj,FLAGS.dataset, FLAGS.measure, FLAGS.nb_node_samples,FLAGS.node_start)


            # # Weights update
            outs = sess.run([opt.opt_op, opt.cost, opt.accuracy],
                            feed_dict = feed_dict)
            # Compute average loss
            avg_cost = outs[1]

            
            if FLAGS.verbose:
                # Display iteration information
                # print("Iter:", '%04d' % (iter + 1), "train_loss=", "{:.5f}".format(avg_cost),
                #      "time=", "{:.5f}".format(time.time() - t))
                # Validation, for link prediction
                if FLAGS.validation and FLAGS.task == 'link_prediction':
                    feed_dict.update({placeholders['dropout']: 0})
                    emb = sess.run(model.z_mean, feed_dict = feed_dict)
                    feed_dict.update({placeholders['dropout']: FLAGS.dropout})
                    val_roc, val_ap = get_roc_score(val_edges, val_edges_false, emb)
                    # print("val_roc=", "{:.5f}".format(val_roc), "val_ap=", "{:.5f}".format(val_ap))
            
            # Get the emb from model
            emb = sess.run(model.z_mean, feed_dict = feed_dict)
        # Get property from subgraph in the adj
            
            subgraph = G.subgraph(sampled_nodes)
            density = nx.density(subgraph)
            sparsity = 1 - density
            sparse_arr.append(density)

            intersection = np.intersect1d(sampled_nodes, sampled_nodes1)
            union = np.union1d(sampled_nodes, sampled_nodes1)
            similar.append(intersection.size/union.size)


        # Compute total running time
        mean_time.append(time.time() - t_start)
        



            
    
        # Test model
        if FLAGS.verbose:
            print("Testing model...")
        # Link prediction: classification edges/non-edges
        if FLAGS.task == 'link_prediction':
            # Get ROC and AP scores
            roc_score, ap_score = get_roc_score(test_edges, test_edges_false, emb)
            # Report scores
            mean_roc.append(roc_score)
            mean_ap.append(ap_score)
            mean_similar.append(np.mean(similar))

        
        # Node clustering: k-means clustering in embedding space
        elif FLAGS.task == 'node_clustering':
            # Clustering in embedding space
            mi_score = clustering_latent_space(emb, labels)
            # Report Adjusted Mutual Information (AMI)
            mean_mutual_info.append(mi_score)
    
    auc.append(np.mean(mean_roc))
    ap.append(np.mean(mean_ap))
    times.append(np.mean(mean_time))
    sparse_array.append(np.mean(sparse_arr))
    similarity.append(np.mean(mean_similar))

    if FLAGS.fastgae =='rn':
        break
path = '/public/chenjiawen/tst/cndp/sampled/%s/node/'%(FLAGS.dataset)
if not os.path.exists(path):
    try:
        os.makedirs(path)
    except OSError as error:
        print(f"Failed to create directory {path}. Error: {error}")
file = '/public/chenjiawen/tst/cndp/sampled/%s/node/%s_%s_%s_%s_%s_%s.npy'%(FLAGS.dataset,FLAGS.dataset,FLAGS.times,FLAGS.fastgae,FLAGS.model,FLAGS.measure,FLAGS.nb_node_samples)
np.save(file,sampled_nodes)
np.save('sampled_nodes.npy', sampled_nodes)


subgraph = G.subgraph(sampled_nodes)
coeff_sub = fit_power_law(subgraph)
coeff_G = fit_power_law(G)
coeff_diff = np.abs(coeff_sub - coeff_G)
r_L,r_C, sparsity, avg_degree, avg_cluster, avg_spl = subgraph_property(subgraph)




# Report final results
print("\n Test results for", FLAGS.model,
      "model on", FLAGS.dataset, "on", FLAGS.task, "\n",
      "___________________________________________________\n")

if FLAGS.task == 'link_prediction':
    print("AUC scores\n", mean_roc)
    print("Mean AUC score: ", np.mean(mean_roc),
          "\nStd of AUC scores: ", np.std(mean_roc), "\n \n")

    print("AP scores\n", mean_ap)
    print("Mean AP score: ", np.mean(mean_ap),
          "\nStd of AP scores: ", np.std(mean_ap), "\n \n")

    # Save the link_prediction results

 
    if FLAGS.fastgae =='sparse':
        filename = '/public/chenjiawen/tst/cndp/sampled/%s/accuracy/%s_%s_%s_sparse_lp_test.txt'%(FLAGS.dataset,FLAGS.dataset,FLAGS.model,FLAGS.measure)
        file = open(filename,'a')
        file.write('%s  %f   %f  %f  %f  %f  %f  %f  %f  '% (FLAGS.nb_node_samples,np.mean(sparse_arr), np.mean(mean_centrality) ,FLAGS.alpha,np.mean(mean_roc),np.std(mean_roc),np.mean(mean_ap),np.std(mean_ap),np.mean(mean_time) ))
        file.write('\n')
        file.close()
    elif FLAGS.fastgae in ['sn','rn','tn','mcmc','gn']:
        path = '/public/chenjiawen/tst/cndp/sampled/%s/accuracy/'%(FLAGS.dataset)
        if not os.path.exists(path):
            try:
                os.makedirs(path)
            except OSError as error:
                print(f"Failed to create directory {path}. Error: {error}")
        # filename = '/public/chenjiawen/tst/cndp/sampled/%s/accuracy/%s_%s_%s_%s_lp_test.txt'%(FLAGS.dataset,FLAGS.dataset,FLAGS.model,FLAGS.measure,FLAGS.fastgae)
        filename = '/public/chenjiawen/tst/cndp/sampled/%s/accuracy/%s_%s_%s_lp_test.txt'%(FLAGS.dataset,FLAGS.dataset,FLAGS.model,FLAGS.fastgae)
        file = open(filename,'a')
        file.write('%s  %s  %f  %f  %f  %f  %f  %f  %f  %f  %f  %f  %f  %f  %f  %f\n' % (
            FLAGS.nb_node_samples,
            FLAGS.measure,
            sparsity,
            avg_degree,
            avg_cluster,
            avg_spl,
            coeff_diff,
            r_C,
            r_L,
            FLAGS.alpha,
            np.mean(auc),
            np.std(auc),
            np.mean(ap),
            np.std(ap),
            np.mean(times),
            np.std(times)
        ))
        # file.write('\n')
        file.close()
    elif FLAGS.fastgae =='un':
        path = '/public/chenjiawen/tst/cndp/sampled/%s/accuracy/'%(FLAGS.dataset)
        if not os.path.exists(path):
            try:
                os.makedirs(path)
            except OSError as error:
                print(f"Failed to create directory {path}. Error: {error}")
        filename = '/public/chenjiawen/tst/cndp/sampled/%s/accuracy/%s_%s_%s_un_lp_test.txt'%(FLAGS.dataset,FLAGS.dataset,FLAGS.model,FLAGS.measure)
        file = open(filename,'a')
        file.write('%s  %f  %f   %f   %f   %f  %f   %f  %f  %f  %f  %f'% (FLAGS.nb_node_samples,np.mean(sparse_array), coeff_diff ,r_C,r_L, FLAGS.alpha,np.mean(auc),np.std(auc),np.mean(ap),np.std(ap),np.mean(times),np.std(times) ))
        file.write('\n')
        file.close()
    else:
        raise ValueError('Undefined Strategy!')
    
    
if FLAGS.fastgae in  ['un','sn','tn','rn','gn','mcmc']:
    path = '/public/chenjiawen/tst/cndp/sampled/%s/similar/'%(FLAGS.dataset)
    if not os.path.exists(path):
        try:
            os.makedirs(path)
        except OSError as error:
            print(f"Failed to create directory {path}. Error: {error}")
    filename = '/public/chenjiawen/tst/cndp/sampled/%s/similar/%s_%s_%s_%s_%s_similar.txt' % (FLAGS.dataset,FLAGS.dataset,FLAGS.fastgae, FLAGS.model, FLAGS.measure, FLAGS.alpha)
    file = open(filename, 'a')
    file.write('%s     %f   %f   '%(FLAGS.nb_node_samples,np.mean(similarity),np.std(similarity)))
    file.write('\n')
    file.close()



    

else:
    print("Adjusted MI scores\n", mean_mutual_info)
    print("Mean Adjusted MI score: ", np.mean(mean_mutual_info),
          "\n Std of Adjusted MI scores: ", np.std(mean_mutual_info), "\n \n")
    filename = '/public/chenjiawen/tst/cndp/sampled/%s_%s_%s_node cluster_test.txt'%(FLAGS.dataset,FLAGS.model,FLAGS.measure)
    file = open(filename,'a')
    file.write('%s %f  %f  %f  %f '% (FLAGS.nb_node_samples,FLAGS.alpha,np.mean(mean_mutual_info),np.std(mean_mutual_info),np.mean(mean_time) ))
    file.write('\n')
    file.close()


print("Total Running times\n", mean_time)
print("Mean total running time: ", np.mean(mean_time),
      "\nStd of total running time: ", np.std(mean_time), "\n")




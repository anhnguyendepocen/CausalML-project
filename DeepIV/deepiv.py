#Attempt to fit a deep IV model to an empirical dataset
#v0.1 - attempt to fit mixture density network a la Bishop (1994)

#heavily inspired by 
#http://blog.otoro.net/2015/11/24/mixture-density-networks-with-tensorflow/
import math
import numpy as np 
import pandas as pd
import tensorflow as tf
import matplotlib.pyplot as plt
import mdn #library for mixture density network estimation I made


#given set of features, predict our DNN conditional distribution for the first stage.
#useful for recovering parameters on new data
def predict_1stStage_cond_dist(features,W_features,B_features,W_hidden,B_hidden):
    hidden = np.tanh(np.dot(features,W_features) + B_features)
    output = np.dot(hidden,W_hidden) + B_hidden
    probs,means,sds = np.split(output,3,axis=1)
    sds = np.exp(sds)
    probs = np.exp(probs)/np.sum(np.exp(probs),axis=1)[:,np.newaxis]
    return probs,means,sds


#stdize variables that are not dummies to be stdizing mean 0 and sd 1,
#and remove all covariates that have no variation in the data
#so that all behave well when fed into DNN
def process_features(features):
    collin_vars = [] #indices of variables with no variation
    feature_means = [] #store means we normalize
    feature_sds = [] #store SDs we normalize
    for v in range(features.shape[1]):
        #remove variables with one unique value- they mess stuff up later
        if len(np.unique(features[:,v].astype(np.float32)))==1:
            collin_vars.append(v)
            continue
        #skip normalizations for dummies (although I guess it doesn't really matter)
        is_dummy = (np.unique(features[:,v].astype(np.float32))==np.array([0.,1.]))   
        if isinstance(is_dummy,bool):
            if is_dummy:
                feature_means.append(0) #for dummies do not transform
                feature_sds.append(1)
                continue
        else:
            if is_dummy.all():
                feature_means.append(0) #for dummies do not transform
                feature_sds.append(1)
                continue  
        feature_means.append(np.mean(features[:,v])) #for dummies do not transform
        feature_sds.append(np.std(features[:,v]))    
        features[:,v] = (features[:,v] - np.mean(features[:,v]))/np.std(features[:,v])
    return [features,feature_means,feature_sds,collin_vars]



#estimate loss function (for validation training)
#args: test_y is true outcome data,
#outcome dnn is the DNN trained to predict y, given inputs
#session is the current tensorflow session being used
#features2 is the set of 2nd stage features,
#probs/means/sds1 are the first stage cond. distro parameters,
#B is the number of simulations per obs
#p_index is the column index of the policy variable we simulate in the feature matrix
def secondstage_loss(outcome,outcome_dnn,inputs,session,features2,probs1,means1,sds1,B=1000,p_index=0):
    mc_outcomes = np.zeros(shape = (outcome.shape[0],B))
    mc_policy = mdn.sim_mdn(probs1,means1,sds1,num_sims=B)
    temp_features = features2
    for b in range(B):
        temp_features[:,p_index] = mc_policy[:,b]
        mc_outcomes[:,b] = session.run(outcome_dnn,feed_dict={inputs: temp_features.astype(np.float32)}).flatten()
    pred_y_hat = np.mean(mc_outcomes,axis=1)
    pred_y_hat.shape = [pred_y_hat.shape[0],1]
    return np.mean((pred_y_hat - outcome)**2.)

#fcn for gradient for SGD
#args: outcome is the real data, 
#features2 are second stage features
#p_index is the location of policy variable in the feature matrix
#pi/mu/sigam1 are conditional distro of each obs
#outcome_dnn is the output layer of 2nd stage dnn fcn
#grad_fcn calculates the gradients of the loss
#B is number of simulations for the gradient
#session is cur tf session
#currently accepts just one observation 
#NOT FINISHED; SEE BELOW WHERE B=1 AND N=1
def ind_secondstage_loss_gradient(outcome,features2,pi1,mu1,sigma1, \
        outcome_dnn,inputs,grad_fcn,session,p_index=0):
    #correct one obs issue w/ array instead of mat
    #print pi1.shape
    p1 = mdn.sim_mdn(pi1,mu1,sigma1,num_sims=1)
    #print pi1.shape
    p2 = mdn.sim_mdn(pi1,mu1,sigma1,num_sims=1)
    tempfeat_1 = features2
    tempfeat_2 = features2
    tempfeat_1[:,p_index] = p1
    tempfeat_2[:,p_index] = p2
    #print"-----"
    pred_outcome = session.run(outcome_dnn,feed_dict={inputs: tempfeat_1.astype(np.float32)})
    grad = session.run(grad_fcn,feed_dict={inputs: tempfeat_2.astype(np.float32)})
    #print(grad)
    multiplier = -2.* (outcome - pred_outcome)
    newgrad=[]
    for g in range(len(grad)):
        newgrad.append(multiplier*grad[g])
    return newgrad

#workflow:
#need to estimate the 2nd stage loss function; do this by, 
#initialize dnn to random one including all features besides the instruments.
#in loop:
# 1.sampling 1 obs per epoch
# 2.sampling to policy outcomes per obs
# 3.calculating gradient of DNN w.r.t. this obs via tf
# 4.step in that direction

# 5. to evaluate the loss for the CV step, across all obs, draw from the conditional distro
#    a lot of times, calc outcome dnn for each, use this to estimate the integral, then subtract from truth squared
#
#rinse and repeat a million times or whatever
# and from each drawing a policy variable; then calculating the gradient of the DNN of 2nd stage on each 
# obs.

#args:
#y: outcome
#p: the endogenous policy variable
#features_second: the covariates for the second stage (should be 1st column is p, the rest are x controls)
#if this must be changed, additional arg is available (p_index denotes col to train)
#pi,mu,sigma: the rows of each individual's 1st stage distribution of the endogenous variable (expressed as mix of normals)
#num_nodes: the number of nodes in the hidden layer
def train_second_stage(y,features_second,pi,mu,sigma,num_nodes,seed=None,p_index=0):
    if seed!=None:
        np.random.seed(seed)       
    #some test code below
    num_inputs = features_second.shape[1] #the number of input features
    num_output = 1 # output layer (currently just one since outcome is one variable)
    num_obs = y.shape[0]


    #initialize weights and biases for input->hidden layer
    W_input = tf.Variable(tf.random_uniform(shape=[num_inputs,num_nodes],minval=-.1,maxval=.1,dtype=tf.float32,seed=seed),name='W_in')
    b_input = tf.Variable(tf.random_uniform(shape=[1,num_nodes],minval=-.1,maxval=.1,dtype=tf.float32,seed=seed),name='B_in')
    #initialize weights and biases for hidden->output layer
    W_output = tf.Variable(tf.random_uniform(shape=[num_nodes,num_output],minval=-.1,maxval=.1,dtype=tf.float32,seed=seed),name='W_out')
    b_output = tf.Variable(tf.random_uniform(shape=[1,num_output],minval=-.1,maxval=.1,dtype=tf.float32,seed=seed),name='B_out')
    #instantiate data vars
    inputs = tf.placeholder(dtype=tf.float32, shape=[None,num_inputs], name="inputs")
    outcome = tf.placeholder(dtype=tf.float32, shape=[None,1], name="outcome")
    #define the function for the hidden layer
    #use canonical tanh function for intermed, simple linear combo for final layer
    hidden_layer = tf.nn.tanh(tf.matmul(inputs, W_input) + b_input)
    outcome_layer = tf.matmul(hidden_layer,W_output) + b_output
    #the gradients of the output layer w.r.t. network parameters
    nn_gradients = tf.gradients(outcome_layer, [W_input, b_input,W_output,b_output]) #the gradients of the DNN w.r.t. parameters
    #placeholders for gradients I pass from numpy
    g_W_in = tf.placeholder(dtype=tf.float32, shape=W_input.get_shape(), name="g_W_in")
    g_b_in = tf.placeholder(dtype=tf.float32, shape=b_input.get_shape(), name="g_b_in")
    g_W_out = tf.placeholder(dtype=tf.float32, shape=W_output.get_shape(), name="g_W_out")
    g_b_out = tf.placeholder(dtype=tf.float32, shape=b_output.get_shape(), name="g_b_out")
    #the gradient-parameter pairs for gradient computation/application
    grad_var_pairs = zip([g_W_in,g_b_in,g_W_out,g_b_out],[W_input,b_input,W_output,b_output])

    #the optimizer
    trainer = tf.train.GradientDescentOptimizer(learning_rate=.001)
    #initialize tensorflow
    s = tf.InteractiveSession()
    s.run(tf.global_variables_initializer())


    validation_losses=[]
    validation_indices = np.random.choice(num_obs,num_obs/5)
    train_indices = np.ones(len(y), np.bool)
    train_indices[validation_indices]=0
    y_validation = y[validation_indices]
    features_validation = features_second[validation_indices,:]
    y_train = y[train_indices]
    features_train  = features_second[train_indices,:]
    num_train_obs = sum(train_indices)
    print "training..."
    num_iters = 10000
    for i in range(num_iters):
        if i%100==0:
            print "iteration" + str(i)
        #extract observation features for SGD
        g_ind=np.random.choice(num_train_obs,1)[0]
        obs_feat= features_second[train_indices,:][g_ind,:]
        obs_y = y[train_indices][g_ind]
        pi_i = pi[train_indices,:][g_ind,:]
        mu_i = mu[train_indices,:][g_ind,:]
        sd_i = sigma[train_indices,:][g_ind,:]
        #reshape everything so treated as 2d
        for v in [obs_y, obs_feat, pi_i, mu_i ,sd_i]:
            v.shape = [1,len(v)]

        stoch_grad = ind_secondstage_loss_gradient(obs_y,obs_feat,pi_i,mu_i,sd_i,outcome_layer,inputs,nn_gradients,s)
        grad_dict={}
        grad_index=0
        for theta in [g_W_in,g_b_in,g_W_out,g_b_out]:
            grad_dict[theta]=stoch_grad[grad_index]
            grad_index+=1
        s.run(trainer.apply_gradients(grad_var_pairs),feed_dict=grad_dict)
        #the gradients of the output layer w.r.t. network parameters
        if i%10==0:
            loss=secondstage_loss(y[validation_indices],outcome_layer,inputs,s,\
                features_second[validation_indices,:], \
                pi[validation_indices,:], \
                mu[validation_indices,:], \
                sigma[validation_indices,:],B=100)
            validation_losses.append(loss)
            if len(validation_losses) > 5:
                if max(validation_losses[(len(validation_losses)-6):(len(validation_losses)-2)])< validation_losses[len(validation_losses)-1]:
                    print "Exiting at iteration " + str(i) + " due to increase in validation error." 
                    break
    plt.plot(range(len(validation_losses)),validation_losses)
    plt.show()
    #recover parameters and return them
    W_in_final = s.run(W_input)
    B_in_final = s.run(b_input)
    W_out_final = s.run(W_output)
    B_out_final = s.run(b_output)
    return [W_in_final, B_in_final, W_out_final,B_out_final]
import datetime
import math

import numpy as np
import tensorflow as tf


class TensorFlowData(object):
    def __init__(self, predictors, response, shuffle_each_epoch=True):
        self.predictors = predictors
        self.num_predictors = self.predictors.shape[1]
        self.response = response

        self.batch_index = 0
        self.reached_end = False
        self.num_observations = self.predictors.shape[0]
        self.shuffle_each_epoch = shuffle_each_epoch

    def get_batch(self, batch_size):
        batch_start, batch_end = self.batch_index, self.batch_index + batch_size
        batch_data = {"predictors:0": np.reshape(self.predictors[batch_start:batch_end, :],
                                                 (1, batch_size, self.num_predictors)),
                      "response:0": np.reshape(self.response[batch_start:batch_end, :],
                                               (batch_size, ))}
        self._update_batch_index(batch_size)
        return batch_data

    def _update_batch_index(self, batch_size):
        self.batch_index += batch_size
        if self.batch_index + batch_size > self.num_observations:
            self.batch_index = 0
            self.reached_end = True
            if self.shuffle_each_epoch:
                self.shuffle()

    def has_reached_end(self):
        result = self.reached_end
        self.reached_end = False
        return result

    def reset_batch_index(self):
        self.reached_end = False
        self.batch_index = 0

    def shuffle(self, random_seed=None):
        if random_seed:
            np.random.seed(random_seed)
        new_order = np.random.permutation(self.num_observations)
        self.predictors = self.predictors[new_order, :]
        self.response = self.response[new_order, :]


class TensorFlowModel(object):
    def __init__(self):
        self.graph = tf.Graph()
        self.fit_log = {"batch_number": [],
                        "validation_score": []}
        self.fit_time = None

    def train(self, train_data, validation_data, training_steps=1000, print_every=200):
        """Train the model.
        Inputs:
            train_data: TensorFloWData object with training data.
            validation_date: TensorFlowData object with validation data.
            training_steps: Number of batches to train.
            print_every: How often to calculate and show validation results.
        """
        start_time = datetime.datetime.now()
        current_step = 0
        while current_step < training_steps:
            self._train_steps(train_data, print_every)
            current_step += print_every
            current_score = self.score(validation_data)
            print("{:>7} - {:0.4f}".format(current_step, current_score))
            self.fit_log["batch_number"].append(current_step)
            self.fit_log["validation_score"].append(current_score)

        end_time = datetime.datetime.now()
        self.fit_time = (end_time - start_time).total_seconds()

    def _train_steps(self, train_data, num_steps):
        for _ in range(num_steps):
            input_dict = train_data.get_batch(self.batch_size)
            input_dict["dropout:0"] = self.dropout_rate
            _, loss = self.session.run([self.optimizer, self.loss], feed_dict=input_dict)

    def score(self, data):
        """Score the model.
        Inputs:
            data: A TensorFlowData object with data to score the model on.
        """
        scores = []
        while not data.has_reached_end():
            input_dict =data.get_batch(self.batch_size)
            input_dict["dropout:0"] = 1.0
            loss = self.session.run([self.loss], feed_dict=input_dict)
            scores.append(loss)
        return math.sqrt(np.mean(scores))

    def save(self, filename):
        """Save the values of all model variables to a file."""
        # TODO: Create folder if it doesn't exist
        self.saver.save(self.session, filename)

    def restore(self, filename):
        """Restore the values of all model variables from a file."""
        self.saver.restore(self.session, filename)

    @staticmethod
    def random_variable(shape, stddev=0.01):
        return tf.Variable(tf.random_normal(shape, stddev=stddev))


class BrainTree(TensorFlowModel):
    def __init__(self, num_features, num_trees, max_depth,
                 batch_size=32, learning_rate=0.001, dropout_rate=0.5):
        super().__init__()
        self.num_features = num_features
        self.num_trees = num_trees
        self.max_depth = max_depth
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        self.dropout_rate = dropout_rate

        with self.graph.as_default():
            self._build_graph()
            self.session = tf.Session(graph=self.graph)
            self.saver = tf.train.Saver()
            self.session.run(tf.global_variables_initializer())
        self.node_names = [node.name + ":0" for node in self.graph.as_graph_def().node]

    def _build_graph(self):
        self.predictors, self.response = self._build_inputs()
        self.dropout = tf.placeholder(tf.float32, None, name="dropout")

        # Model parameters
        self.split_weight = [self.random_variable([2 ** i, self.num_features, self.num_trees])
                             for i in range(self.max_depth)]
        self.split_bias = [self.random_variable([2 ** i, 1, self.num_trees])
                           for i in range(self.max_depth)]
        self.split_strength = [self.random_variable([2 ** i, 1, self.num_trees])
                               for i in range(self.max_depth)]
        self.terminal_weight = self.random_variable([2 ** self.max_depth, self.num_features,
                                                     self.num_trees])
        self.terminal_bias = self.random_variable([2 ** self.max_depth, 1, self.num_trees])

        # Optimization
        self.pred = self._build_predictions()
        self.loss = tf.losses.mean_squared_error(self.pred, self.response)
        self.optimizer = tf.train.AdagradOptimizer(self.learning_rate).minimize(self.loss)

    def _build_predictions(self):
        split_prob = tf.stack([self._build_split_prob(depth)
                               for depth in range(self.max_depth)], axis=3)
        terminal_prob = tf.reduce_prod(split_prob, axis=3)
        terminal_pred = tf.matmul(tf.gather(self.predictors, [0] * (2 ** self.max_depth)),
                                  self.terminal_weight) + self.terminal_bias
        return tf.reduce_sum(terminal_prob * terminal_pred, axis=[0, 2])

    def _build_split_prob(self, depth):
        basic_logit = tf.matmul(tf.gather(self.predictors, [0] * (2 ** depth)),
                                self.split_weight[depth]) \
                      + tf.tile(self.split_bias[depth], [1, self.batch_size, 1])
        prob = tf.sigmoid(basic_logit * tf.exp(self.split_strength[depth]))
        prob_with_complement = tf.concat([prob, 1 - prob], axis=0)
        return tf.gather(prob_with_complement,
                         [i for i in range(2 ** (depth + 1))] * (2 ** (self.max_depth - depth - 1)))

    def _build_inputs(self):
        predictors = tf.placeholder(tf.float32, shape=[1, self.batch_size, self.num_features],
                                    name="predictors")
        response = tf.placeholder(tf.float32, shape=[self.batch_size], name="response")
        return predictors, response
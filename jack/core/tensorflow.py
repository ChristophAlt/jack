import logging
import sys
from abc import abstractmethod
from typing import Iterable, Tuple, List, Mapping, Sequence

import numpy as np
import tensorflow as tf

from jack.core import JTReader, QASetting, Answer, Ports, ModelModule, SharedResources, TensorPort
from jack.core.reader import logger


class TFModelModule(ModelModule):
    """This class represents an  abstract ModelModule for tensroflow models which requires the implementation of
    a small set of methods that produce the TF graphs to create predictions and the training outputs,
    and define the ports.
    """

    def __init__(self, shared_resources: SharedResources, sess=None):
        self.shared_resources = shared_resources
        if sess is None:
            session_config = tf.ConfigProto(allow_soft_placement=True)
            session_config.gpu_options.allow_growth = True
            sess = tf.Session(config=session_config)
        self.tf_session = sess
        # will be set in setup
        self._tensors = None
        self._placeholders = None

    def __call__(self, batch: Mapping[TensorPort, np.ndarray],
                 goal_ports: List[TensorPort] = None) -> Mapping[TensorPort, np.ndarray]:
        """Runs a batch and returns values/outputs for specified goal ports.
        Args:
            batch: mapping from ports to values
            goal_ports: optional output ports, defaults to output_ports of this module will be returned

        Returns:
            A mapping from goal ports to tensors.
        """
        goal_ports = goal_ports or self.output_ports
        feed_dict = self.convert_to_feed_dict(batch)
        goal_tensors = [self.tensors[p] for p in goal_ports
                        if p in self.output_ports or p in self.training_output_ports]
        outputs = self.tf_session.run(goal_tensors, feed_dict)

        ret = dict(zip(filter(lambda p: p in self.output_ports or p in self.training_output_ports, goal_ports),
                       outputs))
        for p in goal_ports:
            if p not in ret and p in batch:
                ret[p] = batch[p]

        return ret

    @abstractmethod
    def create_output(self, shared_resources: SharedResources,
                      *input_tensors: tf.Tensor) -> Sequence[tf.Tensor]:
        """
        This function needs to be implemented in order to define how the module produces
        output from input tensors corresponding to `input_ports`.

        Args:
            *input_tensors: a list of input tensors.

        Returns:
            mapping from defined output ports to their tensors.
        """
        raise NotImplementedError

    @abstractmethod
    def create_training_output(self, shared_resources: SharedResources,
                               *training_input_tensors: tf.Tensor) -> Sequence[tf.Tensor]:
        """
        This function needs to be implemented in order to define how the module produces tensors only used
        during training given tensors corresponding to the ones defined by `training_input_ports`, which might include
        tensors corresponding to ports defined by `output_ports`. This sub-graph should only be created during training.

        Args:
            *training_input_tensors: a list of input tensors.

        Returns:
            mapping from defined training output ports to their tensors.
        """
        raise NotImplementedError

    def setup(self, is_training=True):
        """Sets up the module.

        This usually involves creating the actual tensorflow graph. It is expected to be called after the input module
        is set up and shared resources, such as the vocab, config, etc., are prepared already at this point.
        """
        old_train_variables = tf.trainable_variables()
        old_variables = tf.global_variables()
        if "name" in self.shared_resources.config:
            with tf.variable_scope(self.shared_resources.config["name"],
                                   initializer=tf.contrib.layers.xavier_initializer()):
                self._tensors = {d: d.create_placeholder() for d in self.input_ports}
                output_tensors = self.create_output(
                    self.shared_resources, *[self._tensors[port] for port in self.input_ports])
        else:  # backward compability
            self._tensors = {d: d.create_placeholder() for d in self.input_ports}
            output_tensors = self.create_output(
                self.shared_resources, *[self._tensors[port] for port in self.input_ports])

        self._placeholders = dict(self._tensors)
        self._tensors.update(zip(self.output_ports, output_tensors))
        if is_training:
            if "name" in self.shared_resources.config:
                with tf.variable_scope(self.shared_resources.config["name"]):
                    self._placeholders.update((p, p.create_placeholder()) for p in self.training_input_ports
                                              if p not in self._placeholders and p not in self._tensors)
                    self._tensors.update(self._placeholders)
                    input_target_tensors = {p: self._tensors.get(p, None) for p in self.training_input_ports}
                    training_output_tensors = self.create_training_output(
                        self.shared_resources, *[input_target_tensors[port] for port in self.training_input_ports])
            else:  # backward compability
                self._placeholders.update((p, p.create_placeholder()) for p in self.training_input_ports
                                          if p not in self._placeholders and p not in self._tensors)
                self._tensors.update(self._placeholders)
                input_target_tensors = {p: self._tensors.get(p, None) for p in self.training_input_ports}
                training_output_tensors = self.create_training_output(
                    self.shared_resources, *[input_target_tensors[port] for port in self.training_input_ports])
            self._tensors.update(zip(self.training_output_ports, training_output_tensors))
        self._training_variables = [v for v in tf.trainable_variables() if v not in old_train_variables]
        self._saver = tf.train.Saver(self._training_variables, max_to_keep=1)
        self._variables = [v for v in tf.global_variables() if v not in old_variables]
        self.tf_session.run([v.initializer for v in self.variables])

    @property
    def placeholders(self) -> Mapping[TensorPort, tf.Tensor]:
        return self._placeholders

    @property
    def tensors(self) -> Mapping[TensorPort, tf.Tensor]:
        return self._tensors if hasattr(self, "_tensors") else None

    def store(self, path):
        self._saver.save(self.tf_session, path)

    def load(self, path):
        self._saver.restore(self.tf_session, path)

    @property
    def train_variables(self) -> Sequence[tf.Tensor]:
        return self._training_variables

    @property
    def variables(self) -> Sequence[tf.Tensor]:
        return self._variables

    def convert_to_feed_dict(self, mapping: Mapping[TensorPort, np.ndarray]) -> Mapping[tf.Tensor, np.ndarray]:
        result = {ph: mapping[port] for port, ph in self.placeholders.items() if port in mapping}
        return result


class TFReader(JTReader):
    """Tensorflow implementation of JTReader.

    A tensorflow reader reads inputs consisting of questions, supports and possibly candidates, and produces answers.
    It consists of three layers: input to tensor (input_module), tensor to tensor (model_module), and tensor to answer
    (output_model). These layers are called in-turn on a given input (list).
    """

    @property
    def model_module(self) -> TFModelModule:
        return super().model_module

    @property
    def session(self) -> tf.Session:
        """Returns: input module"""
        return self.model_module.tf_session

    def train(self, optimizer,
              training_set: Iterable[Tuple[QASetting, List[Answer]]],
              batch_size: int, max_epochs=10, hooks=tuple(),
              l2=0.0, clip=None, clip_op=tf.clip_by_value, **kwargs):
        """
        This method trains the reader (and changes its state).

        Args:
            optimizer: TF optimizer
            training_set: the training instances.
            batch_size: size of training batches
            max_epochs: maximum number of epochs
            hooks: TrainingHook implementations that are called after epochs and batches
            l2: whether to use l2 regularization
            clip: whether to apply gradient clipping and at which value
            clip_op: operation to perform for clipping
        """
        logger.info("Setting up data and model...")
        if not self._is_setup:
            # First setup shared resources, e.g., vocabulary. This depends on the input module.
            self.setup_from_data(training_set, is_training=True)
        batches = self.input_module.batch_generator(training_set, batch_size, is_eval=False)
        logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
        loss = self.model_module.tensors[Ports.loss]

        if l2:
            loss += tf.add_n([tf.nn.l2_loss(v) for v in self.model_module.train_variables]) * l2

        if clip:
            gradients = optimizer.compute_gradients(loss)
            if clip_op == tf.clip_by_value:
                gradients = [(tf.clip_by_value(grad, clip[0], clip[1]), var)
                             for grad, var in gradients if grad]
            elif clip_op == tf.clip_by_norm:
                gradients = [(tf.clip_by_norm(grad, clip), var)
                             for grad, var in gradients if grad]
            min_op = optimizer.apply_gradients(gradients)
        else:
            min_op = optimizer.minimize(loss)

        # initialize non model variables like learning rate, optimizer vars ...
        self.session.run([v.initializer for v in tf.global_variables() if v not in self.model_module.variables])

        logger.info("Start training...")
        for i in range(1, max_epochs + 1):
            for j, batch in enumerate(batches):
                feed_dict = self.model_module.convert_to_feed_dict(batch)
                current_loss, _ = self.session.run([loss, min_op], feed_dict=feed_dict)
                for hook in hooks:
                    hook.at_iteration_end(i, current_loss, set_name='train')

            # calling post-epoch hooks
            for hook in hooks:
                hook.at_epoch_end(i)

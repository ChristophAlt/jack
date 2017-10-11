"""Tensor ports are used to define 'module signatures'

Modules are loosly coupled with each other via the use of tensor ports. They simply define what kind of tensors are
produced at the input and/or output of each module, thus defining a kind of signature. This allows for maximum
flexibility when (re-)using modules in different combinations.
"""
import logging
import tensorflow as tf


logger = logging.getLogger(__name__)


class TensorPort:
    """A TensorPort defines an input or output tensor for a modules.

    A port defines at least a shape, name, and its data type.
    """

    def __init__(self, dtype, shape, name, doc_string=None, shape_string=None):
        """Create a new TensorPort.

        Args:
            dtype: the (TF) data type of the port.
            shape: the shape of the tensor.
            name: the name of this port (should be a valid TF name)
            doc_string: a documentation string associated with this port
            shape_string: a string of the form [size_1,size_2,size_3] where size_i is a text describing the
                size of the tensor's dimension i (such as "number of batches").
        """
        self.dtype = dtype
        self.shape = shape
        self.name = name
        self.__doc__ = doc_string
        self.shape_string = shape_string

    def create_placeholder(self):
        """Convenience method that produces a placeholder of the type and shape defined by the port.

        Returns: a placeholder of same type, shape and name.
        """
        return tf.placeholder(self.dtype, self.shape, self.name)

    def get_description(self):
        """Returns a multi-line description string of the TensorPort."""

        return "Tensorport '%s'" % self.name + "\n" + \
               "  dtype: " + str(self.dtype) + "\n" + \
               "  shape: " + str(self.shape) + "\n" + \
               "  doc_string: " + str(self.__doc__) + "\n" + \
               "  shape_string: " + str(self.shape_string)

    def __gt__(self, port):
        return self.name > port.name

    def __repr__(self):
        return "<TensorPort (%s)>" % self.name


class TensorPortWithDefault(TensorPort):
    """
    TensorPort that also defines a default value.
    """

    def __init__(self, default_value, dtype, shape, name, doc_string=None, shape_string=None):
        self.default_value = default_value
        super().__init__(dtype, shape, name, doc_string=doc_string, shape_string=shape_string)

    def create_placeholder(self):
        """Creates a TF placeholder_with_default.

        Convenience method that produces a constant of the type, value and shape defined by the port.
        Returns: a constant tensor of same type, shape and name. It can nevertheless be fed with external values
        as if it was a placeholder.
        """
        ph = tf.placeholder_with_default(self.default_value, self.shape, self.name)
        if ph.dtype != self.dtype:
            logger.warning(
                "Placeholder {} with default of type {} created for TensorPort with type {}!".format(self.name,
                                                                                                     ph.dtype,
                                                                                                     self.dtype))
        return ph


class Ports:
    """Defines sopme common ports for reusability and as examples. Readers can of course define their own.

    This class groups input ports. Different modules can refer to these ports
    to define their input or output, respectively.
    """

    loss = TensorPort(tf.float32, [None], "loss",
                      "Represents loss on each instance in the batch",
                      "[batch_size]")

    class Input:
        question = TensorPort(tf.int32, [None, None], "question",
                              "Represents questions using symbol vectors",
                              "[batch_size, max_num_question_tokens]")

        multiple_support = TensorPort(tf.int32, [None, None, None], "multiple_support",
                                      ("Represents instances with multiple support documents",
                                       " or single instances with extra dimension set to 1"),
                                      "[batch_size, max_num_support, max_num_tokens]")

        atomic_candidates = TensorPort(tf.int32, [None, None], "candidates",
                                       ("Represents candidate choices using single symbols. ",
                                        "This could be a list of entities from global entities ",
                                        "for example atomic_candidates = [e1, e7, e83] from ",
                                        "global_entities = [e1, e2, e3, ..., eN-1, eN"),
                                       "[batch_size, num_candidates]")

        sample_id = TensorPort(tf.int32, [None], "sample_id",
                               "Maps this sample to the index in the input text data",
                               "[batch_size]")

        support_length = TensorPort(tf.int32, [None, None], "support_length",
                                    "Represents length of supports in each support in batch",
                                    "[batch_size, num_supports]")

        question_length = TensorPort(tf.int32, [None], "question_length",
                                     "Represents length of questions in batch",
                                     "[Q]")

    class Prediction:
        logits = TensorPort(tf.float32, [None, None], "candidate_scores",
                            "Represents output scores for each candidate",
                            "[batch_size, num_candidates]")

        candidate_index = TensorPort(tf.float32, [None], "candidate_idx",
                                     "Represents answer as a single index",
                                     "[batch_size]")

    class Target:
        candidate_1hot = TensorPort(tf.float32, [None, None], "candidate_targets",
                                    "Represents target (0/1) values for each candidate",
                                    "[batch_size, num_candidates]")

        target_index = TensorPort(tf.int32, [None], "target_index",
                                  ("Represents symbol id of target candidate. ",
                                   "This can either be an index into a full list of candidates,",
                                   " which is fixed, or an index into a partial list of ",
                                   "candidates, for example a list of potential entities ",
                                   "from a list of many candiadtes"),
                                  "[batch_size]")


class FlatPorts:
    """Flat ports that can be used more flexibly in some cases.

    Number of questions in batch is Q, number of supports is S, number of answers is A, number of candidates is C.
    Typical input ports such as support, candidates, answers are defined together with individual mapping ports. This
    allows for more flexibility when numbers can vary between questions. Naming convention is to use suffix "_flat".
    """

    class Input:
        support_to_question = TensorPort(tf.int32, [None], "support2question",
                                         "Represents mapping to question idx per support",
                                         "[S]")
        candidate_to_question = TensorPort(tf.int32, [None], "candidate2question",
                                           "Represents mapping to question idx per candidate",
                                           "[C]")
        answer2question = TensorPort(tf.int32, [None], "answer2question",
                                     "Represents mapping to question idx per answer",
                                     "[A]")

        support = TensorPort(tf.int32, [None, None], "support_flat",
                             "Represents instances with a single support document. "
                             "[S, max_num_tokens]")

        atomic_candidates = TensorPort(tf.int32, [None], "candidates_flat",
                                       "Represents candidate choices using single symbols",
                                       "[C]")

        seq_candidates = TensorPort(tf.int32, [None, None], "seq_candidates_flat",
                                    "Represents candidate choices using single symbols",
                                    "[C, max_num_tokens]")

        support_length = TensorPort(tf.int32, [None], "support_length_flat",
                                    "Represents length of support in batch",
                                    "[S]")

        question_length = TensorPort(tf.int32, [None], "question_length_flat",
                                     "Represents length of questions in batch",
                                     "[Q]")

    class Prediction:
        candidate_scores = TensorPort(tf.float32, [None], "candidate_scores_flat",
                                      "Represents output scores for each candidate",
                                      "[C]")

        candidate_idx = TensorPort(tf.float32, [None], "candidate_predictions_flat",
                                   "Represents groundtruth candidate labels, usually 1 or 0",
                                   "[C]")

        # extractive QA
        start_scores = TensorPort(tf.float32, [None, None], "start_scores_flat",
                                  "Represents start scores for each support sequence",
                                  "[S, max_num_tokens]")

        end_scores = TensorPort(tf.float32, [None, None], "end_scores_flat",
                                "Represents end scores for each support sequence",
                                "[S, max_num_tokens]")

        answer_span = TensorPort(tf.int32, [None, 2], "answer_span_prediction_flat",
                                 "Represents answer as a (start, end) span", "[A, 2]")

        # generative QA
        generative_symbol_scores = TensorPort(tf.int32, [None, None, None], "symbol_scores",
                                              "Represents symbol scores for each possible "
                                              "sequential answer given during training",
                                              "[A, max_num_tokens, vocab_len]")

        generative_symbols = TensorPort(tf.int32, [None, None], "symbol_prediction",
                                        "Represents symbol sequence for each possible "
                                        "answer target_indexpredicted by the model",
                                        "[A, max_num_tokens]")

    class Target:
        candidate_idx = TensorPort(tf.int32, [None], "candidate_targets_flat",
                                   "Represents groundtruth candidate labels, usually 1 or 0",
                                   "[C]")

        answer_span = TensorPort(tf.int32, [None, 2], "answer_span_target_flat",
                                 "Represents answer as a (start, end) span", "[A, 2]")

        seq_answer = TensorPort(tf.int32, [None, None], "answer_seq_target_flat",
                                "Represents answer as a sequence of symbols",
                                "[A, max_num_tokens]")

        generative_symbols = TensorPort(tf.int32, [None, None], "symbol_targets",
                                        "Represents symbol scores for each possible "
                                        "sequential answer given during training",
                                        "[A, max_num_tokens]")

    class Misc:
        # MISC intermediate ports that might come in handy
        # -embeddings
        embedded_seq_candidates = TensorPort(tf.float32, [None, None, None], "embedded_seq_candidates_flat",
                                             "Represents the embedded sequential candidates",
                                             "[C, max_num_tokens, N]")

        embedded_candidates = TensorPort(tf.float32, [None, None], "embedded_candidates_flat",
                                         "Represents the embedded candidates",
                                         "[C, N]")

        embedded_support = TensorPort(tf.float32, [None, None, None], "embedded_support_flat",
                                      "Represents the embedded support",
                                      "[S, max_num_tokens, N]")

        embedded_question = TensorPort(tf.float32, [None, None, None], "embedded_question_flat",
                                       "Represents the embedded question",
                                       "[Q, max_num_question_tokens, N]")
        # -attention, ...

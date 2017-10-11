"""Shared resources are used to store reader all stateful information about a reader and share it between modules.

Examples are include the vocabulary, hyper-parameters or name of a reader that are mostly stored in a configuration
dict. Shared resources are also used later to setup an already saved reader.
"""

import os
import pickle

from jack.util.vocab import Vocab


class SharedResources:
    """Shared resources between modules.

    A class to provide and store generally shared resources, such as vocabularies,
    across the reader sub-modules.
    """

    def __init__(self, vocab: Vocab = None, config: dict = None):
        """
        Several shared resources are initialised here, even if no arguments
        are passed when calling __init__.
        The instantiated objects will be filled by the InputModule.
        - self.config holds hyperparameter values and general configuration
            parameters.
        - self.vocab serves as default Vocabulary object.
        - self.answer_vocab is by default the same as self.vocab. However,
            this attribute can be changed by the InputModule, e.g. by setting
            sepvocab=True when calling the setup_from_data() of the InputModule.
        """
        self.config = config or dict()
        self.vocab = vocab

    def store(self, path):
        """
        Saves all attributes of this object.

        Args:
            path: path to save shared resources
        """
        with open(path, 'wb') as f:
            remaining = {k: self.__dict__[k] for k in self.__dict__ if k != "vocab"}
            pickle.dump(remaining, f, pickle.HIGHEST_PROTOCOL)
        self.vocab.store(path + "_vocab")

    def load(self, path):
        """
        Loads this (potentially empty) resource from path (all object attributes).
        Args:
            path: path to shared resources
        """
        if os.path.exists(path):
            with open(path, 'rb') as f:
                self.__dict__.update(pickle.load(f))

        self.vocab = Vocab()
        self.vocab.load(path + "_vocab")

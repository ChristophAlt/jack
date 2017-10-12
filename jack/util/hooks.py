# -*- coding: utf-8 -*-

import logging
from abc import *
from collections import defaultdict
from datetime import datetime
from time import strftime, localtime
from time import time
from typing import List, Tuple, Mapping

import numpy as np
import tensorflow as tf
from sklearn.metrics import f1_score

from jack.core.reader import JTReader, TFReader
from jack.core.tensorport import TensorPort, FlatPorts, Ports
from jack.data_structures import QASetting, Answer

logger = logging.getLogger(__name__)

"""
TODO -- hooks should also have prefixes so that one can use the same hook with different parameters
"""


class TrainingHook(metaclass=ABCMeta):
    """Serves as Hook interface."""

    @abstractmethod
    def reader(self) -> JTReader:
        """ Returns: JTReader instance"""
        raise NotImplementedError

    @abstractmethod
    def at_epoch_end(self, epoch: int, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def at_iteration_end(self, epoch: int, loss: float, set_name = 'train', **kwargs):
        raise NotImplementedError


class TFTrainingHook(TrainingHook):
    """Serves as Hook interface."""

    @abstractmethod
    def reader(self) -> TFReader:
        """ Returns: JTReader instance"""
        raise NotImplementedError


class TraceHook(TFTrainingHook):
    """Abstract hook class, which implements an update function the summary."""

    def __init__(self, reader, summary_writer=None):
        self._summary_writer = summary_writer
        self._reader = reader
        self.scores = {}

    @property
    def reader(self) -> TFReader:
        return self._reader

    def update_summary(self, current_step, title, value):
        """Adds summary (title, value) to summary writer object.

        Args:
            current_step (int): Current step in the training procedure.
            value (float): Scalar value for the message.
        """
        if self._summary_writer is not None:
            summary = tf.Summary(value=[
                tf.Summary.Value(tag=title, simple_value=value),
            ])
            self._summary_writer.add_summary(summary, current_step)

    def plot(self, ylim=None):
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt
        from pylab import subplot
        number_of_subplots=len(self.scores.keys())
        colors = ['blue', 'green', 'orange']
        patches = []
        for plot_idx, metric in enumerate(self.scores):
            for i, set_name in enumerate(self.scores[metric].keys()):
                data = self.scores[metric][set_name][0]
                time = self.scores[metric][set_name][1]
                patches.append(mpatches.Patch(color=colors[i], label='{0} {1}'.format(set_name, metric)))
                ax1 = subplot(number_of_subplots,1,plot_idx+1)
                ax1.plot(time,data, label='{0}'.format(metric), color=colors[i])
                if ylim != None:
                    plt.ylim(ymin=ylim[0])
                    plt.ylim(ymax=ylim[1])
                plt.xlabel('iter')
                plt.ylabel('{0} {1}'.format(set_name, metric))
        ax1.legend(handles=patches)

        plt.show()

    def add_to_history(self, score_dict, iter_value, epoch, set_name='train'):
        for metric in score_dict:
            if metric not in self.scores: self.scores[metric] = {}
            if set_name not in self.scores[metric]: self.scores[metric][set_name] = [[],[],[]]
            self.scores[metric][set_name][0].append(score_dict[metric])
            self.scores[metric][set_name][1].append(iter_value)
            self.scores[metric][set_name][2].append(epoch)


class LossHook(TraceHook):
    """A hook at prints the current loss and adds it to the summary."""

    def __init__(self, reader, iter_interval=None, summary_writer=None):
        super(LossHook, self).__init__(reader, summary_writer)
        self._iter_interval = iter_interval
        self._acc_loss = { 'train' : 0.0 }
        self._iter = { 'train' : 0 }
        self._epoch_loss = { 'train' : 0.0 }
        self._iter_epoch = { 'train' : 0 }

    def at_iteration_end(self, epoch, loss, set_name = 'train', **kwargs):
        """Prints the loss, epoch, and #calls; adds it to the summary. Loss should be batch normalized."""
        if self._iter_interval is None: return loss
        if set_name not in self._acc_loss:
            self._acc_loss[set_name] = 0.0
            self._iter[set_name] = 0
            self._epoch_loss[set_name] = 0.0
            self._iter_epoch[set_name] = 0

        self._iter_epoch[set_name] += 1
        self._epoch_loss[set_name] += 1
        self._iter[set_name] += 1
        self._acc_loss[set_name] += loss

        if not self._iter[set_name] == 0 and self._iter[set_name] % self._iter_interval == 0:
            loss = self._acc_loss[set_name] / self._iter_interval
            super().add_to_history({'loss' : loss,},
                    self._iter[set_name], epoch, set_name)
            logger.info("Epoch {0}\tIter {1}\t{3} loss {2}".format(epoch,
                self._iter[set_name], loss, set_name))
            self.update_summary(self._iter[set_name], "{0} loss".format(set_name), loss)
            self._acc_loss[set_name] = 0

        ret = (0.0 if self._iter[set_name] == 0 else self._acc_loss[set_name] / self._iter[set_name])

        return ret

    def at_epoch_end(self, epoch, set_name= 'train', **kwargs):
        if self._iter_interval is None:
            loss = self._acc_loss[set_name] / self._iter_interval[set_name]
            logger.info("Epoch {}\tIter {}\t{3} Loss {}".format(epoch,
                self._iter, loss, set_name))
            self.update_summary(self._iter[set_name], "Loss", loss)
            self._epoch_loss[set_name] = 0
            self._iter_epoch[set_name] = 0

        ret = (0.0 if self._iter_epoch[set_name] == 0 else self._epoch_loss[set_name] / self._iter_epoch[set_name])

        return ret


class ExamplesPerSecHook(TraceHook):
    """Prints the examples per sec and adds it to the summary writer."""

    def __init__(self, reader, batch_size, iter_interval=None, summary_writer=None):
        super(ExamplesPerSecHook, self).__init__(reader, summary_writer)
        self._iter_interval = iter_interval
        self._iter = 0
        self.num_examples = iter_interval * batch_size
        self.reset = True

    def __tag__(self):
        return "Speed"

    def at_epoch_end(self, epoch, **kwargs):
        # to eliminate drop in measured speed due to post-epoch hooks:
        # do not execute; reset for use during epochs only
        self.reset = True

    def at_iteration_end(self, epoch, loss, **kwargs):
        """Prints the examples per sec and adds it to the summary writer."""
        self._iter += 1
        if self.reset:
            self.t0 = time()
            self.reset = False
        elif self._iter % self._iter_interval == 0:
            diff = time() - self.t0
            speed = "%.2f" % (self.num_examples / diff)
            logger.info("Epoch {}\tIter {}\tExamples/s {}".format(str(epoch), str(self._iter), str(speed)))
            self.update_summary(self._iter, self.__tag__(), float(speed))
            self.t0 = time()


class ETAHook(TraceHook):
    """Estimates ETA to next checkpoint, epoch end and training end."""

    def __init__(self, reader, iter_interval, iter_per_epoch, max_epochs, iter_per_checkpoint=None,
                 summary_writer=None):
        super(ETAHook, self).__init__(reader, summary_writer)
        self.iter_interval = iter_interval
        self.iter_per_epoch = iter_per_epoch
        self.iter_per_checkpoint = iter_per_checkpoint
        self.iter = 0
        self.epoch = 1
        self.max_epochs = max_epochs
        self.max_iters = max_epochs * iter_per_epoch
        self.start = time()
        self.start_checkpoint = time()
        self.start_epoch = time()
        self.reestimate = True

    def __tag__(self):
        return "ETA"

    def at_epoch_end(self, epoch, **kwargs):
        # to eliminate drop in measured speed due to post-epoch hooks:
        # do not execute; reset for use during epochs only
        self.start_epoch = time()

    def at_iteration_end(self, epoch, loss, **kwargs):
        """Estimates ETA from max_iter vs current_iter."""
        self.iter += 1

        def format_eta(seconds):
            if seconds == float("inf"):
                return "never"
            else:
                seconds, _ = divmod(seconds, 1)
                minutes, seconds = divmod(seconds, 60)
                hours, minutes = divmod(minutes, 60)
                seconds = str(int(seconds))
                minutes = str(int(minutes))
                hours = str(int(hours))

                if len(hours) < 2:
                    hours = "0" + hours
                if len(minutes) < 2:
                    minutes = "0" + minutes
                if len(seconds) < 2:
                    seconds = "0" + seconds

                return "{}:{}:{}".format(hours, minutes, seconds)

        if not self.iter == 0 and self.iter % self.iter_interval == 0:
            current_time = time()

            def get_eta(progress, start_time, name):
                elapsed = current_time - start_time
                eta = elapsed / progress * (1.0 - progress)
                eta_date = strftime("%y-%m-%d %H:%M:%S", localtime(current_time + eta))
                self.update_summary(self.iter, self.__tag__() + "_" + name, float(eta))

                return format_eta(eta), eta_date

            log = "Epoch %d\tIter %d" % (epoch, self.iter)
            total_progress = float(self.iter) / self.max_iters
            eta, eta_data = get_eta(total_progress, self.start, "total")
            log += "\tETA: %s, %s (%.2f%%)" % (eta, eta_data, total_progress * 100)
            epoch_progress = float((self.iter - 1) % self.iter_per_epoch + 1) / self.iter_per_epoch
            eta, _ = get_eta(epoch_progress, self.start_epoch, "epoch")
            log += "\tETA(epoch): %s (%.2f%%)" % (eta, epoch_progress * 100)
            if self.iter_per_checkpoint is not None:
                checkpoint_progress = float((self.iter - 1) % self.iter_per_checkpoint + 1) / self.iter_per_checkpoint
                eta, _ = get_eta(checkpoint_progress, self.start_checkpoint, "checkpoint")
                log += "\tETA(checkpoint): %s (%.2f%%)" % (eta, checkpoint_progress * 100)

            logger.info(log)

        if self.iter_per_checkpoint is not None and self.iter % self.iter_per_checkpoint == 0:
            self.start_checkpoint = time()


class EvalHook(TraceHook):
    def __init__(self, reader: JTReader, dataset, batch_size: int, ports: List[TensorPort],
                 iter_interval=None, epoch_interval=1, metrics=None, summary_writer=None,
                 write_metrics_to=None, info="", side_effect=None):
        super(EvalHook, self).__init__(reader, summary_writer)
        self._total = len(dataset)
        self._dataset = dataset
        self._batches = None
        self._ports = ports
        self._epoch_interval = epoch_interval
        self._iter_interval = iter_interval
        self._batch_size = batch_size
        # self.done_for_epoch = False
        self._iter = 0
        self._info = info or self.__class__.__name__
        self._write_metrics_to = write_metrics_to
        self._metrics = metrics or self.possible_metrics
        self._side_effect = side_effect
        self._side_effect_state = None

    @abstractmethod
    def possible_metrics(self) -> List[str]:
        """Returns: list of metric keys this evaluation hook produces. """
        raise NotImplementedError

    @abstractmethod
    def apply_metrics(self, tensors: Mapping[TensorPort, np.ndarray]) -> Mapping[str, float]:
        """Returns: dict from metric name to float"""
        raise NotImplementedError

    def combine_metrics(self, accumulated_metrics: Mapping[str, List[float]]) -> Mapping[str, float]:
        """Returns:
               dict from metric name to float. Per default batch metrics are simply averaged by
               total number of examples"""
        return {k: sum(vs) / self._total for k, vs in accumulated_metrics.items()}

    def __call__(self, epoch):
        logger.info("Started evaluation %s" % self._info)

        if self._batches is None:
            self._batches = self.reader.input_module.batch_generator(self._dataset, self._batch_size, is_eval=True)

        metrics = defaultdict(lambda: list())
        for i, batch in enumerate(self._batches):
            predictions = self.reader.model_module(batch, self._ports)
            m = self.apply_metrics(predictions)
            for k in self._metrics:
                metrics[k].append(m[k])

        metrics = self.combine_metrics(metrics)
        super().add_to_history(metrics, self._iter, epoch)

        printmetrics = sorted(metrics.keys())
        res = "Epoch %d\tIter %d\ttotal %d" % (epoch, self._iter, self._total)
        for m in printmetrics:
            res += '\t%s: %.3f' % (m, metrics[m])
            self.update_summary(self._iter, self._info + '_' + m, metrics[m])
            if self._write_metrics_to is not None:
                with open(self._write_metrics_to, 'a') as f:
                    f.write("{0} {1} {2:.5}\n".format(datetime.now(), self._info + '_' + m,
                                                      np.round(metrics[m], 5)))
        res += '\t' + self._info
        logger.info(res)

        if self._side_effect is not None:
            self._side_effect_state = self._side_effect(metrics, self._side_effect_state)

    def at_epoch_end(self, epoch: int, **kwargs):
        if self._epoch_interval is not None and epoch % self._epoch_interval == 0:
            self.__call__(epoch)

    def at_test_time(self, epoch):
        self.__call__(epoch)

    def at_iteration_end(self, epoch: int, loss: float, **kwargs):
        self._iter += 1
        if self._iter_interval is not None and self._iter % self._iter_interval == 0:
            self.__call__(epoch)


class XQAEvalHook(EvalHook):
    """This evaluation hook computes the following metrics: exact and per-answer f1 on token basis."""

    def __init__(self, reader: JTReader, dataset: List[Tuple[QASetting, List[Answer]]], batch_size: int,
                 iter_interval=None, epoch_interval=1, metrics=None, summary_writer=None,
                 write_metrics_to=None, info="", side_effect=None, **kwargs):
        ports = [FlatPorts.Prediction.answer_span, FlatPorts.Target.answer_span, FlatPorts.Input.answer2question]
        super().__init__(reader, dataset, batch_size, ports, iter_interval, epoch_interval, metrics, summary_writer,
                         write_metrics_to, info, side_effect)

    @property
    def possible_metrics(self) -> List[str]:
        return ["exact", "f1"]

    @staticmethod
    def preferred_metric_and_best_score():
        return 'f1', [0.0]

    def apply_metrics(self, tensors: Mapping[TensorPort, np.ndarray]) -> Mapping[str, float]:
        correct_spans = tensors[FlatPorts.Target.answer_span]
        predicted_spans = tensors[FlatPorts.Prediction.answer_span]
        correct2prediction = tensors[FlatPorts.Input.answer2question]

        def len_np_or_list(v):
            if isinstance(v, list):
                return len(v)
            else:
                return v.shape[0]

        acc_f1 = 0.0
        acc_exact = 0.0
        k = 0
        for i in range(len_np_or_list(predicted_spans)):
            f1, exact = 0.0, 0.0
            p_start, p_end = predicted_spans[i][0], predicted_spans[i][1]
            while k < len_np_or_list(correct_spans) and correct2prediction[k] == i:
                c_start, c_end = correct_spans[k][0], correct_spans[k][1]
                if p_start == c_start and p_end == c_end:
                    f1 = 1.0
                    exact = 1.0
                elif f1 < 1.0:
                    total = float(c_end - c_start + 1)
                    missed_from_start = float(p_start - c_start)
                    missed_from_end = float(c_end - p_end)
                    tp = total - min(total, max(0, missed_from_start) + max(0, missed_from_end))
                    fp = max(0, -missed_from_start) + max(0, -missed_from_end)
                    recall = tp / total
                    precision = tp / (tp + fp + 1e-10)
                    f1 = max(f1, 2.0 * precision * recall / (precision + recall + 1e-10))
                k += 1

            acc_f1 += f1
            acc_exact += exact

        return {"f1": acc_f1, "exact": acc_exact}


class ClassificationEvalHook(EvalHook):
    def __init__(self, reader: JTReader, dataset: List[Tuple[QASetting, List[Answer]]], batch_size: int,
                 iter_interval=None, epoch_interval=1, metrics=None, summary_writer=None,
                 write_metrics_to=None, info="", side_effect=None, **kwargs):

        ports = [Ports.Prediction.logits,
                 Ports.Prediction.candidate_index,
                 Ports.Target.target_index]

        super().__init__(reader, dataset, batch_size, ports, iter_interval, epoch_interval, metrics, summary_writer,
                         write_metrics_to, info, side_effect)

    @property
    def possible_metrics(self) -> List[str]:
        return ["Accuracy", "F1_macro"]

    @staticmethod
    def preferred_metric_and_best_score():
        return 'Accuracy', [0.0]

    def apply_metrics(self, tensors: Mapping[TensorPort, np.ndarray]) -> Mapping[str, float]:
        labels = tensors[Ports.Target.target_index]
        predictions = tensors[Ports.Prediction.candidate_index]

        acc_exact = np.sum(np.equal(labels, predictions))
        acc_f1 = f1_score(labels, predictions, average='macro') * labels.shape[0]

        return {"F1_macro": acc_f1, "Accuracy": acc_exact}


class KBPEvalHook(EvalHook):
    def __init__(self, reader: JTReader, dataset: List[Tuple[QASetting, List[Answer]]], batch_size: int,
                 iter_interval=None, epoch_interval=1, metrics=None, summary_writer=None,
                 write_metrics_to=None, info="", side_effect=None, **kwargs):
        ports = [Ports.loss]
        self.epoch = 0
        super().__init__(reader, dataset, batch_size, ports, iter_interval, epoch_interval, metrics, summary_writer,
                         write_metrics_to, info, side_effect)

    @property
    def possible_metrics(self) -> List[str]:
        return ["log_p"]

    @staticmethod
    def preferred_metric_and_best_score():
        return 'log_p', [float('-inf')]

    def apply_metrics(self, tensors: Mapping[TensorPort, np.ndarray]) -> Mapping[str, float]:
        loss = tensors[Ports.loss]
        return {"log_p": -np.sum(loss)}

    def at_test_time(self, epoch, vocab=None):
        from scipy.stats import rankdata
        from numpy import asarray

        logger.info("Started test evaluation %s" % self._info)

        if self._batches is None:
            self._batches = self.reader.input_module.batch_generator(self._dataset, self._batch_size, is_eval=True)

        def len_np_or_list(v):
            if isinstance(v, list):
                return len(v)
            else:
                return v.shape[0]

        q_cand_scores = {}
        q_cand_ids = {}
        q_answers = {}
        qa_scores = []
        qa_ids = []
        for i, batch in enumerate(self._batches):
            predictions = self.reader.model_module(
                batch, [Ports.Input.question, Ports.Target.target_index, Ports.Prediction.logits,
                        Ports.Input.atomic_candidates])
            correct_answers = predictions[Ports.Target.target_index]
            logits = predictions[Ports.Prediction.logits]
            candidate_ids = predictions[Ports.Input.atomic_candidates]
            questions = predictions[Ports.Input.question]
            for j in range(len_np_or_list(questions)):
                q=questions[j][0]
                q_cand_scores[q] = logits[j]
                q_cand_ids[q] = candidate_ids[j]
                if q not in q_answers:
                    q_answers[q] = set()
                q_answers[q].add(correct_answers[j])
        for q in q_cand_ids:
            for k,c in enumerate(q_cand_ids[q]):
                qa = str(q) + "\t" + str(c)
                qa_ids.append(qa)
                qa_scores.append(q_cand_scores[q][k])
        qa_ranks = rankdata(- asarray(qa_scores), method="min")
        qa_rank = {}
        for i,qa_id in enumerate(qa_ids):
            qa_rank[qa_id]=qa_ranks[i]
        mean_ap = 0
        wmap = 0
        qd = 0
        md = 0
        for q in q_answers:
            cand_ranks = rankdata(- q_cand_scores[q], method="min")
            ans_ranks=[]
            for a in q_answers[q]:
                for c, cand in enumerate(q_cand_ids[q]):
                    if a == cand and cand_ranks[c] <= 100:
                        ans_ranks.append(cand_ranks[c])
            av_p = 0
            answers = 1
            for r in sorted(ans_ranks):
                p = answers/r
                av_p = av_p+p
                answers += 1
            if len(ans_ranks) > 0:
                wmap = wmap+av_p
                md = md+len(ans_ranks)
                av_p = av_p/len(ans_ranks)
                res = '\t%s\t%s: %.3f (%d)' % (self.reader.shared_resources.vocab.get_sym(q),"Average Precision", av_p,len(q_answers[q]))
                logger.info(res)
                qd = qd + 1
            else:
                pass
            mean_ap = mean_ap + av_p
        q_answers_len = len(q_answers)
        mean_ap = mean_ap / q_answers_len if q_answers_len else 0
        wmap = wmap / md if md else 0
        res = "Epoch %d\tIter %d\ttotal %d" % (epoch, self._iter, self._total)
        res += '\t%s: %.3f' % ("Mean Average Precision", mean_ap)
        self.update_summary(self._iter, self._info + '_' + "Mean Average Precision", mean_ap)
        if self._write_metrics_to is not None:
            with open(self._write_metrics_to, 'a') as f:
                f.write("{0} {1} {2:.5}\n".format(datetime.now(), self._info + '_' + "Mean Average Precision",
                                                  np.round(mean_ap, 5)))
        res += '\t' + self._info
        logger.info(res)
        res = "Epoch %d\tIter %d\ttotal %d" % (epoch, self._iter, self._total)
        res += '\t%s: %.3f' % ("Weighted Mean Average Precision", wmap)
        self.update_summary(self._iter, self._info + '_' + "Weighted Mean Average Precision", wmap)
        if self._write_metrics_to is not None:
            with open(self._write_metrics_to, 'a') as f:
                f.write("{0} {1} {2:.5}\n".format(datetime.now(), self._info + '_' + "Weighted Mean Average Precision",
                                                  np.round(wmap, 5)))
        res += '\t' + self._info
        logger.info(res)

    def at_epoch_end(self, epoch: int, **kwargs):
        self.epoch += 1
        if self._epoch_interval is not None and epoch % self._epoch_interval == 0:
            self.__call__(epoch)

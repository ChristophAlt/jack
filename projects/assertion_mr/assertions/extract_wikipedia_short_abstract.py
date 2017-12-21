import re
from bz2 import BZ2File
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import spacy

from projects.assertion_mr.assertions.store import AssertionStore


def uncamel(name):
    return re.sub('([a-z0-9])([A-Z])', r'\1 \2', name)


def write_assertions(abstracts, labels, store, max_tokens=50):
    # nlp = spacy.load('en', disable=['parser', 'ner', 'textcat'])
    nlp = spacy.load('en', parser=False)

    lemma_labels = dict()
    counter = 0

    for article, assertion in abstracts.items():
        if counter % 100000 == 0:
            logger.info('%d assertions added' % counter)
            if counter > 0:
                store._assertion_db['wikipedia_firstsent'].sync()
        subjects = set()
        ll = labels.get(article)
        if ll is None or not ll:
            ll = [uncamel(article).replace('_', ' ')]
        for l in ll:
            if l not in lemma_labels:
                lemma_labels[l] = ' '.join(t.lemma_ for t in nlp(l))
            subjects.add(lemma_labels[l])
        store.add_assertion(assertion, subjects, [], 'wikipedia_firstsent')
        counter += 1

if __name__ == '__main__':
    import logging
    import os
    import sys
    import tensorflow as tf

    logger = logging.getLogger(os.path.basename(sys.argv[0]))
    logging.basicConfig(level=logging.INFO)

    tf.app.flags.DEFINE_string('assertion_store_path', None, 'directory to assertion store')
    tf.app.flags.DEFINE_string('dbpedia_short_abstracts', None, 'path to dbpedia short abstracts')
    tf.app.flags.DEFINE_string('dbpedia_anchor_texts', None, 'path to dbpedia anchor texts')
    tf.app.flags.DEFINE_string('dbpedia_transitive_redirect', None, 'path to dbpedia transitive redirects')
    tf.app.flags.DEFINE_integer('max_articles_per_anchor', 3, 'maximum number of articles per anchor')
    tf.app.flags.DEFINE_integer('min_num_anchor_per_article', 10,
                                'minimum number of mentions per anchor for an article')
    tf.app.flags.DEFINE_integer('max_abstract_tokens', 50, 'maximum number of tokens to take from abstract')

    FLAGS = tf.app.flags.FLAGS

    pool = ProcessPoolExecutor(4)


    def myopen(fn):
        return BZ2File(fn) if fn.endswith('.bz2') else open(fn)


    logger.info('Loading DBpedia abstracts...')


    def process_abstract(l):
        l = l.decode('utf-8')
        if l.startswith('#'):
            return None, None
        split = l.split('> ')
        article = split[0][split[0].rindex('/') + 1:]
        if article.startswith('List_of') or '(disambiguation)' in article or '__' in article:
            return None, None
        abstract = split[2]
        abstract = abstract[1:abstract.find('"@en')]
        abstract = ' '.join(abstract.split()[:FLAGS.max_abstract_tokens])
        return article, abstract


    abstracts = dict()
    with myopen(FLAGS.dbpedia_short_abstracts) as f:
        for article, abstract in pool.map(process_abstract, f, chunksize=100000):
            if article is not None:
                abstracts[article] = abstract
    pool.shutdown()
    del pool

    anchor2articles = defaultdict(lambda: defaultdict(int))


    def process_line(l):
        l = l.decode('utf-8')
        if l.startswith('#'):
            return None, None
        split = l.split('> ')
        subj = split[0][split[0].rindex('/') + 1:]
        if subj.startswith('List_of') or '(disambiguation)' in subj or '__' in subj:
            return None, None
        obj = split[2]
        if obj.startswith('"'):
            obj = obj[1:obj.find('"@en')]
        else:
            obj = obj[obj.rindex('/') + 1:]
        return subj, obj


    logger.info('Loading DBpedia redirects...')
    transitive_redirects = dict()
    with myopen(FLAGS.dbpedia_transitive_redirect) as f:
        ct = 0
        for subj, obj in map(process_line, f):
            if subj is not None and not (obj.startswith('List_of') or '(disambiguation)' in obj or '__' in obj):
                transitive_redirects[subj] = obj
            ct += 1
            if ct % 1000000 == 0:
                logger.info('%d lines processed...' % ct)

    logger.info('Loading DBpedia anchor texts for articles...')

    with myopen(FLAGS.dbpedia_anchor_texts) as f:
        ct = 0
        for article, anchor_text in map(process_line, f):
            if article is not None:
                anchor2articles[anchor_text.lower()][transitive_redirects.get(article, article)] += 1
            ct += 1
            if ct % 1000000 == 0:
                logger.info('%d lines processed...' % ct)

    logger.info('Selecting Top-%d articles for anchors that were at least %d times linked...' %
                (FLAGS.max_articles_per_anchor, FLAGS.min_num_anchor_per_article))
    labels = defaultdict(set)
    for anchor, articles in anchor2articles.items():
        articles = sorted([(a, ct) for a, ct in articles.items() if ct > FLAGS.min_num_anchor_per_article],
                          key=lambda x: -x[1])[:FLAGS.max_articles_per_anchor]
        for a, _ in articles:
            labels[a].add(anchor)
    del anchor2articles

    logger.info('Extending DBpedia labels with redirects...')
    for k, vs in transitive_redirects.items():
        for v in vs:
            labels[v].add(uncamel(k).replace('_', ' ').lower())
    del transitive_redirects

    logger.info('Writing first wikipedia sentences for %d entities...' % len(abstracts))
    store = AssertionStore(FLAGS.assertion_store_path, writeback=True)
    write_assertions(abstracts, labels, store)
    del abstracts
    del labels
    store.save()

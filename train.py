from __future__ import print_function
import time
import torch.optim as optim
import codecs
# from model.stack_lstm import *
from model.batch_stack_lstm import *
import model.utils as utils
import model.evaluate as evaluate

import argparse
import os
import sys
from tqdm import tqdm
import itertools
import functools





if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Training transition-based NER system')
    parser.add_argument('--batch', action='store_true')
    parser.add_argument('--rand_embedding', action='store_true', help='random initialize word embedding')
    parser.add_argument('--emb_file', default='embedding/sskip.100.vectors',
                        help='path to pre-trained embedding')
    parser.add_argument('--train_file', default='data/conll2003/train.txt', help='path to training file')
    parser.add_argument('--dev_file', default='data/conll2003/dev.txt', help='path to development file')
    parser.add_argument('--test_file', default='data/conll2003/test.txt', help='path to test file')
    parser.add_argument('--batch_size', type=int, default=100, help='batch size (10)')
    parser.add_argument('--gpu', type=int, default=0, help='gpu id, set to -1 if use cpu mode')
    parser.add_argument('--unk', default='unk', help='unknow-token in pre-trained embedding')
    parser.add_argument('--checkpoint', default='./checkpoint/ner_', help='path to checkpoint prefix')
    parser.add_argument('--hidden', type=int, default=100, help='hidden dimension')
    parser.add_argument('--char_hidden', type=int, default=50, help='hidden dimension for character')
    parser.add_argument('--char_structure',  choices=['lstm', 'cnn'], default='lstm', help='')
    parser.add_argument('--drop_out', type=float, default=0.5, help='dropout ratio')
    parser.add_argument('--epoch', type=int, default=50, help='maximum epoch number')
    parser.add_argument('--start_epoch', type=int, default=0, help='start epoch idx')
    parser.add_argument('--caseless', default=True, help='caseless or not')
    parser.add_argument('--spelling', default=True, help='use spelling or not')
    parser.add_argument('--embedding_dim', type=int, default=100, help='dimension for word embedding')
    parser.add_argument('--char_embedding_dim', type=int, default=50, help='dimension for char embedding')
    parser.add_argument('--action_embedding_dim', type=int, default=20, help='dimension for action embedding')
    parser.add_argument('--layers', type=int,  default=1, help='number of lstm layers')
    parser.add_argument('--lr', type=float, default=0.001, help='initial learning rate')
    parser.add_argument('--singleton_rate', type=float, default=0.2, help='initial singleton rate')
    parser.add_argument('--lr_decay', type=float, default=0.75, help='decay ratio of learning rate')
    parser.add_argument('--load_check_point', default='', help='path of checkpoint')
    parser.add_argument('--load_opt', action='store_true', help='load optimizer from ')
    parser.add_argument('--update', choices=['sgd', 'adam'], default='adam', help='optimizer method')
    parser.add_argument('--mode', choices=['train', 'predict'], default='train', help='mode selection')
    parser.add_argument('--momentum', type=float, default=0.9, help='momentum for sgd')
    parser.add_argument('--clip_grad', type=float, default=5.0, help='grad clip at')
    parser.add_argument('--mini_count', type=float, default=1, help='thresholds to replace rare words with <unk>')
    parser.add_argument('--eva_matrix', choices=['a', 'fa'], default='fa', help='use f1 and accuracy or accuracy alone')
    parser.add_argument('--patience', type=int, default=15, help='patience for early stop')
    parser.add_argument('--least_iters', type=int, default=50, help='at least train how many epochs before stop')
    parser.add_argument('--shrink_embedding', action='store_true',
                        help='shrink the embedding dictionary to corpus (open this if pre-trained embedding dictionary is too large, but disable this may yield better results on external corpus)')
    args = parser.parse_args()

    print('setting:')
    print(args)

    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    args.checkpoint = args.checkpoint + date.split(' ')[0]
    if not os.path.exists(args.checkpoint):
        os.makedirs(args.checkpoint)

    if_cuda = True if args.gpu >= 0 else False

    # load corpus
    print('loading corpus')
    with codecs.open(args.train_file, 'r', 'utf-8') as f:
        lines = f.readlines()
    with codecs.open(args.dev_file, 'r', 'utf-8') as f:
        dev_lines = f.readlines()
    with codecs.open(args.test_file, 'r', 'utf-8') as f:
        test_lines = f.readlines()

    # converting format
    word_count = dict()
    dev_features, dev_labels, dev_actions, word_count = utils.read_corpus_ner(dev_lines, word_count)
    test_features, test_labels, test_actions, word_count = utils.read_corpus_ner(test_lines, word_count)


    if args.load_check_point:
        if os.path.isfile(args.load_check_point):
            print("loading checkpoint: '{}'".format(args.load_check_point))
            checkpoint_file = torch.load(args.load_check_point)
            args.start_epoch = checkpoint_file['epoch']
            features_map = checkpoint_file['f_map']
            labels_map = checkpoint_file['l_map']
            actions_map = checkpoint_file['a_map']
            ner_map = checkpoint_file['ner_map']
            char_map = checkpoint_file['char_map']
            singleton = checkpoint_file['singleton']
            train_features, train_labels, train_actions, word_count = utils.read_corpus_ner(lines, word_count)
        else:
            print("no checkpoint found at: '{}'".format(args.load_check_point))
    else:
        print('constructing coding table')

        train_features, train_labels, train_actions, features_map, labels_map, actions_map, ner_map,\
        singleton, char_map = utils.generate_corpus(lines, word_count, args.spelling,
                                                    if_shrink_feature=True, thresholds=0)
        #feature_map是专门train_dataset的word_list,labels_map中含有一个<pad>
        f_set = {v for v in features_map}



        # map reduce, map是对每一个对应位置的元素进行，类似zip；reduce针对强两个先做，再将结果和后续的递归计算
        # Add word in dev and in test into feature_map
        dataset_features = functools.reduce(lambda x, y: x | y, map(lambda t: set(t), dev_features),f_set)
        # reduce(func,seq,start_value or init) 上述操作：
        # 首先将dev_features中，每一句话的list变成set，然后以f_set为起始值，合并两个set
        dataset_features = functools.reduce(lambda x, y: x | y, map(lambda t: set(t), test_features), dataset_features)
        dataset_features = functools.reduce(lambda x, y: x | y, map(lambda t: set(t), train_features), dataset_features)
        # 把train_features中的词也加了进来，因为从feature到feature_map可能会将一些低频词替换成<unk>
        # 两个set做合并操作。只不过用到了比较复杂的reduce。 竖线表示“与”，这里表示集合的“并”
        # 得到了data_feature_set, 也就是所有的词的集合



        if not args.rand_embedding:
            print("feature size: '{}'".format(len(features_map)))
            print('loading embedding')
            # features_map = {'<eof>': 0}   ???????????????????
            features_map, embedding_tensor= utils.load_embedding_wlm(args.emb_file, ' ', features_map, dataset_features,
                                                                     args.caseless, args.unk,
                                                                     args.embedding_dim,
                                                                     shrink_to_corpus=args.shrink_embedding)
            print("embedding size: '{}'".format(len(features_map)))

        l_set = functools.reduce(lambda x, y: x | y, map(lambda t: set(t), dev_labels))
        l_set = functools.reduce(lambda x, y: x | y, map(lambda t: set(t), test_labels), l_set)
        for label in l_set:
            if label not in labels_map:
                labels_map[label] = len(labels_map)

    print("%d train sentences" % len(train_features))
    print("%d dev sentences" % len(dev_features))
    print("%d test sentences" % len(test_features))

    # construct dataset
    singleton = list(functools.reduce(lambda x, y: x & y, map(lambda t: set(t), [singleton, features_map])))
    dataset = utils.construct_dataset(train_features, train_labels, train_actions, features_map, labels_map, actions_map, singleton, args.singleton_rate, args.caseless)
    dev_dataset = utils.construct_dataset(dev_features, dev_labels, dev_actions, features_map, labels_map, actions_map, singleton, args.singleton_rate, args.caseless)
    test_dataset = utils.construct_dataset(test_features, test_labels, test_actions, features_map, labels_map, actions_map, singleton, args.singleton_rate, args.caseless)

    dataset_loader = [torch.utils.data.DataLoader(tup, args.batch_size, shuffle=True, drop_last=False) for tup in dataset]
    dev_dataset_loader = [torch.utils.data.DataLoader(tup, args.batch_size, shuffle=False, drop_last=False) for tup in dev_dataset]
    test_dataset_loader = [torch.utils.data.DataLoader(tup, args.batch_size, shuffle=False, drop_last=False) for tup in test_dataset]

    # build model
    print('building model')
    ner_model = TransitionNER(args.mode, actions_map, features_map, labels_map, char_map, ner_map, len(features_map), len(actions_map), args.embedding_dim, args.action_embedding_dim, args.char_embedding_dim, args.hidden, args.char_hidden, args.layers, args.drop_out,
                              args.spelling, args.char_structure, is_cuda=args.gpu)

    if args.load_check_point:
        ner_model.load_state_dict(checkpoint_file['state_dict'])
    else:
        if not args.rand_embedding:
            ner_model.load_pretrained_embedding(embedding_tensor)
        else:
            print('random initialization')
            ner_model.rand_init(init_word_embedding=args.rand_embedding)

    if args.update == 'sgd':
        optimizer = optim.SGD(ner_model.parameters(), lr=args.lr, momentum=args.momentum, nesterov=True)
    elif args.update == 'adam':
        optimizer = optim.Adam(ner_model.parameters(), lr=args.lr, betas=(0.9, 0.9))

    if args.load_check_point and args.load_opt:
        optimizer.load_state_dict(checkpoint_file['optimizer'])

    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, 'min', factor=args.lr_decay, patience=0,
                                                           verbose=True)

    if if_cuda:
        print('device: ' + str(args.gpu))
        torch.cuda.set_device(args.gpu)
        ner_model.cuda(device=args.gpu)
    else:
        if_cuda = False

    tot_length = sum(map(lambda t: len(t), dataset_loader))
    best_f1 = float('-inf')
    best_acc = float('-inf')
    track_list = list()
    start_time = time.time()
    epoch_list = range(args.start_epoch, args.start_epoch + args.epoch)
    patience_count = 0

    for epoch_idx, args.start_epoch in enumerate(epoch_list):

        epoch_loss = 0
        ner_model.train() #将训练模式调整为training mode。只对dropout、batchnorm起作用
    
        for feature, label, action in tqdm(
                itertools.chain.from_iterable(dataset_loader), mininterval=2,
                desc=' - Tot it %d (epoch %d)' % (tot_length, args.start_epoch), leave=False, file=sys.stdout):

            fea_v, la_v, ac_v = utils.repack_vb(if_cuda, feature, label, action)
            ner_model.zero_grad()  # zeroes the gradient of all parameters
            # loss, _, _ = ner_model.forward(fea_v, ac_v)
            loss, _ = ner_model.forward(fea_v, ac_v)
            loss.backward()
            nn.utils.clip_grad_norm(ner_model.parameters(), args.clip_grad)
            optimizer.step()
            epoch_loss += utils.to_scalar(loss)

        # update lr
        scheduler.step(epoch_loss)
        dev_f1, dev_pre, dev_rec = evaluate.calc_f1_score(ner_model, dev_dataset_loader, actions_map, if_cuda)

        if dev_f1 > best_f1:
            patience_count = 0
            if epoch_idx > 0:
                try:
                    os.remove(args.checkpoint + '/dev=' + str(best_f1) + '.json')
                    os.remove(args.checkpoint + '/dev=' + str(best_f1) + '.model')
                except Exception as inst:
                    print(inst)

            best_f1 = dev_f1
            test_f1, test_pre, test_rec = evaluate.calc_f1_score(ner_model, test_dataset_loader, actions_map, if_cuda)

            track_list.append(
                {'loss': epoch_loss, 'dev_f1': dev_f1, 'test_f1': test_f1})

            print(
                '(loss: %.4f, epoch: %d, dev F1 = %.4f, dev pre = %.4f, dev rec = %.4f, F1 on test = %.4f, pre on test = %.4f, rec on test = %.4f), saving...' %
                (epoch_loss,
                 args.start_epoch,
                 dev_f1,
                 dev_pre,
                 dev_rec,
                 test_f1,
                 test_pre,
                 test_rec))

            try:
                utils.save_checkpoint({
                    'epoch': args.start_epoch,
                    'state_dict': ner_model.state_dict(),
                    'optimizer': optimizer.state_dict(),
                    'f_map': features_map,
                    'l_map': labels_map,
                    'a_map': actions_map,
                    'ner_map': ner_map,
                    'char_map': char_map,
                    'singleton': singleton
                }, {'track_list': track_list,
                    'args': vars(args)
                    }, args.checkpoint + '/dev=' + str(round(best_f1*100,2)))
            except Exception as inst:
                print(inst)

        else:
            patience_count += 1
            print('(loss: %.4f, epoch: %d, dev F1 = %.4f)' %
                  (epoch_loss,
                   args.start_epoch,
                   dev_f1))
            track_list.append({'loss': epoch_loss, 'dev_f1': dev_f1})

        print('epoch: ' + str(args.start_epoch) + '\t in ' + str(args.epoch) + ' take: ' + str(
            time.time() - start_time) + ' s')

        if patience_count >= args.patience and args.start_epoch >= args.least_iters:
            break

    # print best
    print(
        args.checkpoint + ' dev_f1: %.4f dev_rec: %.4f dev_pre: %.4f test_f1: %.4f test_rec: %.4f test_pre: %.4f\n' % (
        dev_f1, dev_rec, dev_pre, test_f1, test_rec, test_pre))

    # printing summary
    print('setting:')
    print(args)

    # log_file.close()

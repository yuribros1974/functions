# Generated by nuclio.export.NuclioExporter

import numpy as np
import pandas as pd
import numbers

import sklearn
from sklearn.base import clone
from sklearn.utils import check_random_state

import matplotlib.pyplot as plt
import seaborn as sns

from cloudpickle import load

from mlrun.execution import MLClientCtx
from mlrun.datastore import DataItem
from mlrun.artifacts import get_model, PlotArtifact
from typing import Union, Callable, List


def _get_n_samples_bootstrap(n_samples, max_samples) -> int:
    """get the number of samples in a bootstrap sample

    returns the total number of samples to draw for the bootstrap sample

    private api in sklearn >= v0.24, taken from sklearn.ensemble._forest.py

    :param n_samples:   Number of samples in the dataset.
    :param max_samples:
        The maximum number of samples to draw from the total available:
            - if float, this indicates a fraction of the total and should be
              the interval `(0, 1)`;
            - if int, this indicates the exact number of samples;
            - if None, this indicates the total number of samples.
    """
    if max_samples is None:
        return n_samples

    if isinstance(max_samples, numbers.Integral):
        if not (1 <= max_samples <= n_samples):
            msg = "`max_samples` must be in range 1 to {} but got value {}"
            raise ValueError(msg.format(n_samples, max_samples))
        return max_samples

    if isinstance(max_samples, numbers.Real):
        if not (0 < max_samples < 1):
            msg = "`max_samples` must be in range (0, 1) but got value {}"
            raise ValueError(msg.format(max_samples))
        return int(round(n_samples * max_samples))

    msg = "`max_samples` should be int or float, but got type '{}'"
    raise TypeError(msg.format(type(max_samples)))


def _get_unsampled_ix(random_state, n_samples: int) -> np.array:
    """
    future-proof get unsampled indices
    """
    n_bootstrap = _get_n_samples_bootstrap(n_samples, n_samples)
    random_instance = check_random_state(random_state)
    sample_indices = random_instance.randint(0, n_samples, n_bootstrap)
    sample_counts = np.bincount(sample_indices, minlength=n_samples)

    return np.arange(n_samples)[sample_counts == 0]


def _oob_classifier_accuracy(rf, X_train, y_train) -> float:
    """
    Compute out-of-bag (OOB) accuracy for a scikit-learn forest classifier.

    https://github.com/scikit-learn/scikit-learn/blob/a24c8b46/sklearn/ensemble/forest.py#L425
    """
    X = X_train.values if isinstance(X_train, pd.DataFrame) else X_train
    y = y_train.values if isinstance(y_train, pd.Series) else y_train

    n_samples = len(X)
    n_classes = len(np.unique(y))
    predictions = np.zeros((n_samples, n_classes))
    for tree in rf.estimators_:
        unsampled_indices = _get_unsampled_ix(tree.random_state, n_samples)
        tree_preds = tree.predict_proba(X[unsampled_indices, :])
        predictions[unsampled_indices] += tree_preds

    predicted_class_indexes = np.argmax(predictions, axis=1)
    predicted_classes = [rf.classes_[i] for i in predicted_class_indexes]

    oob_score = np.mean(y == predicted_classes)

    return oob_score


def permutation_importance(
    context: MLClientCtx,
    model: DataItem,
    dataset: DataItem,
    labels: str,
    figsz=(10, 5),
    plots_dest: str = "plots",
    fitype: str = "permute",
) -> pd.DataFrame:
    """calculate change in metric

    type 'permute' uses a pre-estimated model
    type 'dropcol' uses a re-estimates model

    :param context:     the function's execution context
    :param model:       a trained model
    :param dataset:     features and ground truths, regression targets
    :param labels       name of the ground truths column
    :param figsz:       matplotlib figure size
    :param plots_dest:  path within artifact store
    :
    """
    model_file, model_data, _ = get_model(model.url, suffix=".pkl")
    model = load(open(str(model_file), "rb"))

    X = dataset.as_df()
    y = X.pop(labels)
    header = X.columns

    metric = _oob_classifier_accuracy

    baseline = metric(model, X, y)

    imp = []
    for col in X.columns:
        if fitype is "permute":
            save = X[col].copy()
            X[col] = np.random.permutation(X[col])
            m = metric(model, X, y)
            X[col] = save
            imp.append(baseline - m)
        elif fitype is "dropcol":
            X_ = X.drop(col, axis=1)
            model_ = clone(model)
            model_.random_state = random_state
            model_.fit(X_, y)
            o = model_.oob_score_
            imp.append(baseline - o)
        else:
            raise ValueError("unknown fitype, only 'permute' or 'dropcol' permitted")

    zipped = zip(imp, header)
    feature_imp = pd.DataFrame(sorted(zipped), columns=["importance", "feature"])
    feature_imp.sort_values(by="importance", ascending=False, inplace=True)

    plt.clf()
    plt.figure(figsize=figsz)
    sns.barplot(x="importance", y="feature", data=feature_imp)
    plt.title(f"feature importances-{fitype}")
    plt.tight_layout()

    context.log_artifact(
        PlotArtifact(f"feature importances-{fitype}", body=plt.gcf()),
        local_path=f"{plots_dest}/feature-permutations.html",
    )
    context.log_dataset(
        f"feature-importances-{fitype}-tbl", df=feature_imp, index=False
    )

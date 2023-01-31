import warnings

import pandas as pd

from evalml.pipelines.components import CatBoostRegressor
from evalml.utils import SEED_BOUNDS


def test_catboost_regressor_random_seed_bounds_seed(X_y_regression):
    """ensure catboost's RNG doesn't fail for the min/max bounds we support on user-inputted random seeds."""
    X, y = X_y_regression
    col_names = ["col_{}".format(i) for i in range(len(X[0]))]
    X.ww.columns = col_names
    clf = CatBoostRegressor(
        n_estimators=1,
        max_depth=1,
        random_seed=SEED_BOUNDS.min_bound,
    )
    clf.fit(X, y)
    clf = CatBoostRegressor(
        n_estimators=1,
        max_depth=1,
        random_seed=SEED_BOUNDS.max_bound,
    )
    fitted = clf.fit(X, y)
    assert isinstance(fitted, CatBoostRegressor)


def test_catboost_regressor_init_n_jobs():
    n_jobs = 2
    clf = CatBoostRegressor(n_jobs=n_jobs)
    assert clf._component_obj.get_param("thread_count") == n_jobs


def test_catboost_regressor_init_thread_count():
    with warnings.catch_warnings(record=True) as w:
        CatBoostRegressor(thread_count=2)
    assert len(w) == 1
    assert "Parameter 'thread_count' will be ignored. " in str(w[-1].message)


def test_catboost_regressor_double_categories_in_X(categorical_floats_df):
    X = categorical_floats_df
    y = pd.Series([1, 2, 3, 4, 5] * 20)

    clf = CatBoostRegressor()
    fitted = clf.fit(X, y)
    assert isinstance(fitted, CatBoostRegressor)
    predictions = clf.predict(X)
    assert isinstance(predictions, pd.Series)

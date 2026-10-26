from copy import deepcopy

import pytest

from office_synth.config import DEFAULT, taxonomy
from office_synth.pipeline import Pipeline


@pytest.fixture
def config(tmp_path):
    cfg = deepcopy(DEFAULT)
    cfg["_root"] = str(tmp_path / "run")
    cfg["_base"] = str(tmp_path)
    cfg["_taxonomy"] = taxonomy()
    return cfg


@pytest.fixture
def pipeline(config):
    p = Pipeline(config)
    yield p
    p.store.close()


@pytest.fixture
def built(pipeline):
    for stage in ["sample", "collect", "build_seeds", "instantiate", "plan", "generate", "validate"]:
        getattr(pipeline, stage)()
    return pipeline

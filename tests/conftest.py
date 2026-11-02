import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "frappe-desk" / "scripts"))


@pytest.fixture
def app(tmp_path):
	"""Fresh copy of the sample app; returns fdesk_lib.common.App."""
	from fdesk_lib.common import App

	dst = tmp_path / "sample_app"
	shutil.copytree(ROOT / "tests" / "fixtures" / "sample_app", dst)
	return App.find(dst)

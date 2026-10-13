"""Real filesystem link rejection; Windows without link privilege skips explicitly."""
import pytest

from backend.tests import test_restore
from scripts.backup import backup
from scripts.restore_local import restore


@pytest.mark.parametrize('operation', [backup, restore])
@pytest.mark.parametrize('location', ['root', 'ledgerlens.sqlite', 'checkpoints.sqlite', 'sources', 'source_file', 'destination_parent'])
def test_backup_and_restore_reject_real_links_before_publishing(tmp_path, operation, location):
    source = test_restore.backup.__wrapped__(tmp_path)
    destination = tmp_path / 'new-output'
    if location == 'root':
        path, target = tmp_path / 'source-link', source
    elif location == 'destination_parent':
        target = tmp_path / 'real-parent'
        target.mkdir()
        path = tmp_path / 'parent-link'
        destination = path / 'new-output'
    else:
        path = next((source / 'sources').glob('*/*')) if location == 'source_file' else source / location
        target = path.with_name(path.name + '.real')
        path.rename(target)
    try:
        path.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'Filesystem link creation unavailable: {type(exc).__name__}')
    if location == 'root':
        source = path
    with pytest.raises(ValueError, match='links or junctions'):
        operation(source, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob('.new-output-*'))
    assert target.exists()

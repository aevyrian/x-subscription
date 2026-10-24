import os
from contextlib import contextmanager
from pathlib import Path

from gallery_dl import main, postprocessor
from gallery_dl.postprocessor.metadata import MetadataPP


class AtomicMetadataPP(MetadataPP):
    @contextmanager
    def open(self, path):
        pending = path + '.part'
        try:
            with super().open(pending) as stream:
                yield stream
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, path)
        finally:
            Path(pending).unlink(missing_ok=True)


if __name__ == '__main__':
    # Keep gallery-dl's metadata format; replace only its file publishing step.
    postprocessor._cache['metadata'] = AtomicMetadataPP
    raise SystemExit(main())

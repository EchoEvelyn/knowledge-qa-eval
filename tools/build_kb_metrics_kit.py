"""Build an additive, dependency-free Mac metrics toolkit."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ['kb_metrics.py', 'kb_eval.py', 'dify_eval.py', 'qa_eval.py', 'manage.py',
         'knowledge-base/eval/questions.json', 'docs/knowledge-base-metrics.md']
FILES += [str(p.relative_to(ROOT)) for p in sorted((ROOT/'knowledge-base/it-demo').glob('*.txt'))]
FILES += [str(p.relative_to(ROOT)) for folder in ('evaluation/reviews/kb-run-01', 'reports/kb-run-01-metric-audit')
          for p in sorted((ROOT/folder).iterdir()) if p.is_file()]


def build():
    path = ROOT/'knowledge-base/kb-metrics-kit.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 6, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (ROOT/name).read_bytes())
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == set(FILES)
        for name in FILES:
            assert archive.read(name) == (ROOT/name).read_bytes()
    print('Verified metrics kit:', path)


if __name__ == '__main__':
    build()

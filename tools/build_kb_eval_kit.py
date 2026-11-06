"""Package only the scripts/data needed for Mac document-KB evaluation."""
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
FILES=['kb_eval.py','dify_eval.py','qa_eval.py','manage.py',
       'knowledge-base/eval/questions.json','docs/knowledge-base-evaluation.md',
       'knowledge-base/it-demo/01-account-and-mfa.txt',
       'knowledge-base/it-demo/02-network-and-vpn.txt',
       'knowledge-base/it-demo/03-shared-files-and-recovery.txt']


def build():
    path=ROOT/'knowledge-base/kb-evaluation-kit.zip'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            info=zipfile.ZipInfo(name,date_time=(2026,11,6,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,(ROOT/name).read_bytes())
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist())==set(FILES)
        for name in FILES:assert archive.read(name)==(ROOT/name).read_bytes()
    print('Verified Mac kit:',path)


if __name__=='__main__':build()

"""Public install and transcript interface checks; no network or model download."""
import copy
import io
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = next((ROOT / '.agents/skills').iterdir())
sys.path.insert(0, str(SKILL / 'scripts'))
import common
import evidence
import materials
import transcribe
from validate_course import validate

spec = importlib.util.spec_from_file_location('project_installer', ROOT / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class PublicChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='biliscribe-public-')
        self.root = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def prepare(self):
        manifest = ROOT / 'examples/text/course_manifest.json'
        evidence.import_transcript(manifest, 'L001', ROOT / 'examples/text/transcript.srt', self.root / 'transcripts')
        return evidence.prepare(manifest, self.root / 'transcripts', 'transcript-only', self.root / 'evidence.json')
    def test_install_self_contained_and_update_preserves_secrets(self):
        target = installer.install(self.root / 'skills')
        self.assertTrue((target/'scripts/common.py').is_file())
        self.assertTrue((target/'requirements.txt').is_file())
        self.assertTrue((target/'LICENSE').is_file())
        text=(target/'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('name: '+target.name+'\n',text)
        # Installed entry must not contain a link to the source clone.
        self.assertNotIn(str(ROOT), text)
        with self.assertRaises(ValueError):
            installer.install(self.root/'skills')
        secret=target/'.env'
        secret.write_text('ASR_API_KEY=local-test-secret\n',encoding='utf-8')
        installer.install(self.root/'skills',update=True)
        self.assertEqual(secret.read_text(encoding='utf-8'),'ASR_API_KEY=local-test-secret\n')
        # Run copied scripts in another working directory, independent of clone paths.
        result=subprocess.run([sys.executable,str(target/'scripts/evidence.py'),'--help'],cwd=self.root,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_precise_and_coarse_transcript_import(self):
        prepared=self.prepare()
        packet=prepared['packets'][0]
        self.assertFalse(packet['frames'])
        self.assertEqual(packet['identity']['lesson_id'],'L001')
        self.assertTrue(all(row['timing']=='segment' for row in packet['transcript']))
        untimed=self.root/'untimed.txt'
        untimed.write_text('仅有整讲文字。',encoding='utf-8')
        evidence.import_transcript(ROOT/'examples/text/course_manifest.json','L001',untimed,self.root/'untimed')
        data=json.loads(next((self.root/'untimed').glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(data['segments'][0]['timing'],'coarse')
        self.assertEqual(data['segments'][0]['end'],12)
    def test_record_and_changed_evidence_is_rejected(self):
        prepared=self.prepare()
        packet=prepared['packets'][0]
        row=packet['transcript'][0]
        analysis={'packet_id':packet['packet_id'],'mode':'transcript-only','status':'complete','summary':'Offline test record, not a model evaluation.','reviewed_frame_ids':[],
                  'entries':[{'entry_id':packet['packet_id']+'-E1','kind':'concept','text':row['text'],'provenance':'COURSE_SPOKEN','timestamp':row['start'],'transcript_ids':[row['id']],'frame_ids':[],'quote':row['text']}],
                  'operations':[],'contradictions':[],'gaps':[]}
        common.write_json(self.root/'analysis.json',analysis)
        record=evidence.record(self.root/'evidence.json',self.root/'analysis.json',self.root/'understanding.json')
        self.assertEqual(evidence.coverage(prepared,record),[])
        bad=copy.deepcopy(analysis)
        bad['entries'][0]['quote']='不存在于转录的原话'
        with self.assertRaises(ValueError):
            evidence.check_analysis(packet,bad)
        transcript=next((self.root/'transcripts').glob('*.json'))
        data=common.read_json(transcript)
        data['segments'][0]['text']+=' 输入已更改。'
        common.write_json(transcript,data)
        self.assertTrue(evidence.coverage(prepared,record))
    def test_missing_output_does_not_pass(self):
        shutil_source=ROOT/'examples/text/course_manifest.json'
        (self.root/'course_manifest.json').write_bytes(shutil_source.read_bytes())
        errors,_,_=validate(self.root)
        self.assertTrue(errors)
    def test_glossary_keeps_json_and_text_consistent(self):
        self.prepare()
        glossary=self.root/'terms.json'
        glossary.write_text(json.dumps({'以前':'过去'},ensure_ascii=False),encoding='utf-8')
        result=subprocess.run([sys.executable,str(SKILL/'scripts/transcribe.py'),'--apply-glossary',str(glossary),'-o',str(self.root/'transcripts')],capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        data=common.read_json(next((self.root/'transcripts').glob('*.json')))
        self.assertIn('过去',data['segments'][0]['text'])
    def test_glossary_logs_chinese_on_english_windows_console(self):
        self.prepare()
        glossary=self.root/'terms.json'
        glossary.write_text(json.dumps({'以前':'过去'},ensure_ascii=False),encoding='utf-8')
        output=io.BytesIO()
        stream=io.TextIOWrapper(output,encoding='cp1252')
        args=SimpleNamespace(apply_glossary=str(glossary),glossary_min_len=2,inputs=[str(self.root/'transcripts')],outdir=str(self.root/'transcripts'),dry_run=False)
        with patch.object(sys,'stdout',stream),patch.object(sys,'stderr',stream):
            self.assertEqual(transcribe.run_glossary(args),0)
            stream.flush()
            self.assertIn('以前',output.getvalue().decode('utf-8'))
        self.assertIn('过去',common.read_json(next((self.root/'transcripts').glob('*.json')))['segments'][0]['text'])

    def test_audio_rejects_image_mode_and_video_command(self):
        if SKILL.name.endswith('vision'):
            self.skipTest('Audio edition boundary')
        self.prepare()
        with self.assertRaises(ValueError):
            evidence.prepare(ROOT/'examples/text/course_manifest.json',self.root/'transcripts','transcript-images',self.root/'bad.json')
        result=subprocess.run([sys.executable,str(SKILL/'scripts/bili_fetch.py'),'video','BV1234567890'],capture_output=True)
        self.assertEqual(result.returncode,2)
        self.assertFalse((SKILL/'scripts/video_visual.py').exists())
    def test_installed_env_is_loaded_from_copied_skill(self):
        target=installer.install(self.root/'installed')
        (target/'.env').write_text('ASR_BASE_URL=https://example.invalid/custom\n',encoding='utf-8')
        code='import sys, os; sys.path.insert(0,sys.argv[1]); import common; common.load_env(); print(os.environ.get("ASR_BASE_URL"))'
        import os
        env=dict(os.environ)
        env.pop('ASR_BASE_URL',None)
        result=subprocess.run([sys.executable,'-c',code,str(target/'scripts')],cwd=self.root,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'https://example.invalid/custom')

if __name__=='__main__':
    unittest.main()

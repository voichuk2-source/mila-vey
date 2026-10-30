import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, mock_open
import upgrade_3b as upgrade


class UpgradeTests(unittest.TestCase):
    def test_1_5b_trial_with_reported_memory_switches_only_after_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'mila'
            target.mkdir()
            (target/'.mila-managed').touch()
            (target/'.env').write_text('BOT_TOKEN=local-test-value\nOLLAMA_MODEL=qwen2.5:0.5b\n')
            dropin=Path(directory)/'unit.d'/'30-model-3b.conf'
            def answer(body):
                self.assertIn('qwen2.5:0.5b',(target/'.env').read_text())
                self.assertEqual(body['model'],'qwen2.5:1.5b')
                return {'message':{'content':'Hey Alex!'}}
            with patch.object(upgrade,'TARGET',target), patch.object(upgrade,'DROPIN',dropin), \
                 patch.object(upgrade,'MODEL','qwen2.5:1.5b'), \
                 patch.object(upgrade,'AI_LIMIT',1400*upgrade.MIB), \
                 patch.object(upgrade,'RESERVE',640*upgrade.MIB), \
                 patch.object(upgrade,'available_memory',return_value=2146*upgrade.MIB), \
                 patch.object(upgrade,'wait_ai'), patch.object(upgrade,'run'), \
                 patch.object(upgrade,'request',side_effect=answer), \
                 patch.object(upgrade.time,'sleep'), \
                 patch.object(upgrade.os,'geteuid',return_value=0), \
                 patch.object(upgrade.shutil,'disk_usage') as disk, \
                 patch('builtins.open',mock_open()), patch.object(upgrade.fcntl,'flock'), \
                 patch.object(upgrade.sys,'path',upgrade.sys.path.copy()):
                disk.return_value.free=10*1024**3
                self.assertEqual(upgrade.main(),0)
                self.assertIn('OLLAMA_MODEL=qwen2.5:1.5b',(target/'.env').read_text())
                self.assertIn('BOT_TOKEN=local-test-value',(target/'.env').read_text())
                self.assertIn('MemoryMax=1400M',dropin.read_text())

    def test_failed_inference_restores_configuration_and_services(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'mila'
            target.mkdir()
            (target/'.mila-managed').touch()
            original=b'BOT_TOKEN=local-test-value\nOLLAMA_MODEL=qwen2.5:0.5b\n'
            (target/'.env').write_bytes(original)
            dropin=Path(directory)/'unit.d'/'30-model-3b.conf'
            with patch.object(upgrade,'TARGET',target), patch.object(upgrade,'DROPIN',dropin), \
                 patch.object(upgrade,'available_memory',return_value=4*1024**3), \
                 patch.object(upgrade,'wait_ai'), patch.object(upgrade,'run') as run, \
                 patch.object(upgrade,'request',side_effect=RuntimeError('inference failed')), \
                 patch.object(upgrade.os,'geteuid',return_value=0), \
                 patch.object(upgrade.shutil,'disk_usage') as disk, \
                 patch('builtins.open',mock_open()), patch.object(upgrade.fcntl,'flock'), \
                 patch.object(upgrade.sys,'path',upgrade.sys.path.copy()):
                disk.return_value.free=10*1024**3
                with self.assertRaises(RuntimeError): upgrade.main()
                self.assertEqual((target/'.env').read_bytes(),original)
                self.assertFalse(dropin.exists())
                self.assertEqual(run.call_args.args,
                    ('systemctl','start','mila-vey-ai.service','mila-vey.service'))

    def test_insufficient_memory_changes_no_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)
            (target/'.mila-managed').touch()
            (target/'.env').write_text('OLLAMA_MODEL=qwen2.5:0.5b\n')
            with patch.object(upgrade,'TARGET',target), \
                 patch.object(upgrade,'available_memory',return_value=2*1024**3), \
                 patch.object(upgrade,'run') as run, \
                 patch.object(upgrade.os,'geteuid',return_value=0), \
                 patch('builtins.open',mock_open()), patch.object(upgrade.fcntl,'flock'), \
                 patch.object(upgrade.sys,'path',upgrade.sys.path.copy()):
                self.assertEqual(upgrade.main(),2)
                self.assertFalse(any('stop' in call.args for call in run.call_args_list))
                self.assertEqual((target/'.env').read_text(),'OLLAMA_MODEL=qwen2.5:0.5b\n')


if __name__=='__main__': unittest.main()

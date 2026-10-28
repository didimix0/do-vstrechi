from pathlib import Path
import sys,tempfile,threading,unittest
from urllib import request,error
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from do_vstrechi.startup import DEFAULT_PORT,runtime_revision,matching_server,make_legacy_redirect
from do_vstrechi.webapp import TrainerApp,make_server,serve
from do_vstrechi.cli import main
from test_webapp import FakeAnalyzer


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.app=TrainerApp(Path(self.folder.name)/'test.sqlite3',FakeAnalyzer)
        self.server=make_server(self.app,0);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.redirect=make_legacy_redirect(self.server.server_port,0);self.redirect_thread=threading.Thread(target=self.redirect.serve_forever,daemon=True);self.redirect_thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}';self.old=f'http://127.0.0.1:{self.redirect.server_port}'
        self.opener=request.build_opener(request.ProxyHandler({}))
    def tearDown(self):
        for server,thread in ((self.redirect,self.redirect_thread),(self.server,self.thread)):
            server.shutdown();server.server_close();thread.join(timeout=3)
        self.folder.cleanup()
    def test_old_bookmark_opens_current_page_and_assets(self):
        with self.opener.open(self.old+'/?check=1',timeout=3) as response:
            self.assertEqual(response.geturl(),self.base+'/?check=1')
            self.assertIn(b'vtb-theme.css',response.read())
            self.assertEqual(response.headers['Cache-Control'],'no-store')
        with self.opener.open(self.old+'/vtb-theme.css',timeout=3) as response:self.assertEqual(response.status,200)
    def test_rejects_wrong_host_and_does_not_forward_posts(self):
        for req,expected in ((request.Request(self.old+'/',headers={'Host':'attacker.example'}),403),
                             (request.Request(self.old+'/api/sessions',data=b'{}',headers={'Content-Type':'application/json'}),409)):
            with self.assertRaises(error.HTTPError) as exc:self.opener.open(req,timeout=3)
            self.assertEqual(exc.exception.code,expected)
        self.assertEqual(self.app.store.recent(),[])
    def test_only_matching_application_revision_is_reused(self):
        self.assertTrue(matching_server(self.base,self.app.runtime_revision))
        self.assertFalse(matching_server(self.base,'old-revision'))
    def test_second_server_cannot_bind_to_the_same_address(self):
        with self.assertRaises(OSError):make_server(self.app,self.server.server_port)
    def test_repeated_start_opens_running_current_app(self):
        with patch('do_vstrechi.webapp.TrainerApp',return_value=self.app),patch('do_vstrechi.webapp.load_local_environment'),patch('do_vstrechi.webapp.webbrowser.open') as opened:
            self.assertEqual(serve(self.server.server_port,True),0)
        opened.assert_called_once_with(self.base)
    def test_menu_and_cli_use_the_current_default_port(self):
        with patch('do_vstrechi.webapp.serve',return_value=0) as started:
            self.assertEqual(main(['--serve']),0)
        started.assert_called_once_with(DEFAULT_PORT,False,None)
        self.assertEqual(DEFAULT_PORT,8766)
    def test_revision_changes_with_styles_and_does_not_read_credentials(self):
        root=Path(self.folder.name)/'files';(root/'web').mkdir(parents=True)
        css=root/'web/app.css';css.write_text('body{color:blue}',encoding='utf-8')
        first=runtime_revision(root);(root/'.env').write_text('not-a-real-key',encoding='utf-8')
        self.assertEqual(runtime_revision(root),first)
        css.write_text('body{color:navy}',encoding='utf-8');self.assertNotEqual(runtime_revision(root),first)

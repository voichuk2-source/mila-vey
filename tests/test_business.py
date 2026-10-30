import tempfile
import time
import unittest
from pathlib import Path
from bot import Bot, Store


class Transport:
    def __init__(self):
        self.calls=[]
        self.connection={'id':'connection-1','user':{'id':900,'username':'mila_account'},'is_enabled':True,'rights':{'can_reply':True}}
    def call(self,method,**args):
        self.calls.append((method,args))
        return self.connection if method=='getBusinessConnection' else True
    def send(self,chat,text,**kw):
        return self.call('sendMessage',chat_id=chat,text=text,**kw)

class AI:
    def __init__(self): self.calls=[]; self.on_reply=None
    def reply(self,history):
        self.calls.append(history)
        if self.on_reply: self.on_reply()
        return 'Hey, how are you?'

class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.s=Store(Path(self.temp.name)/'db')
        self.tg=Transport(); self.ai=AI()
        self.bot=Bot(self.tg,self.s,self.ai,1)
        self.bot.first_reply_delay=0
        self.bot.save_business_connection(self.tg.connection,notify=False)
    def tearDown(self): self.s.db.close(); self.temp.cleanup()
    def message(self,**changes):
        msg={'business_connection_id':'connection-1','message_id':77,'from':{'id':1},
            'chat':{'type':'private','id':1},'date':time.time(),'text':'Hi Mila'}
        msg.update(changes)
        return {'business_message':msg}
    def approve(self): self.bot.business_admin(1,'/approve_business 900')
    def sent(self): return [p for m,p in self.tg.calls if m=='sendMessage' and p.get('business_connection_id')]
    def test_named_tester_pinned_by_id_without_admin_access(self):
        self.approve()
        self.bot.business_admin(1,'/test_business @Obnibn')
        self.bot.handle(self.message(**{'from':{'id':2,'username':'OBNIBN'}}))
        self.assertEqual(self.s.get('business_tester_id'),'2')
        self.assertEqual(len(self.sent()),1)
        self.assertFalse(self.bot.business_admin(2,'/public on'))
        self.assertFalse(self.bot.allowed(2))
        self.bot.handle(self.message(message_id=78,**{'from':{'id':3,'username':'Obnibn'}}))
        self.assertEqual(len(self.sent()),1)
        self.bot.handle(self.message(message_id=79,**{'from':{'id':2,'username':'changed_name'}}))
        self.assertEqual(len(self.sent()),2)
        self.bot.business_admin(1,'/test_business_off')
        self.bot.handle(self.message(message_id=80,**{'from':{'id':2,'username':'Obnibn'}}))
        self.assertEqual(len(self.sent()),2)
    def test_no_processing_before_admin_approval(self):
        self.bot.handle(self.message()); self.assertEqual(self.ai.calls,[])
    def test_new_session_after_six_hours_delays_read_and_keeps_memory(self):
        from unittest.mock import patch
        self.tg.connection['rights']['can_read_messages']=True
        self.approve()
        now=time.time()
        with patch('business.time.time',return_value=now):
            self.bot.handle(self.message(text='My name is Alex'))
        self.bot.first_reply_delay=60
        with patch('business.time.time',return_value=now+6*3600):
            self.bot.handle(self.message(message_id=78,text='Back again'))
            self.bot.flush_business_pending()
        self.assertEqual(len(self.sent()),1)
        self.assertEqual(len([m for m,_ in self.tg.calls if m=='readBusinessMessage']),1)
        with patch('business.time.time',return_value=now+6*3600+30):
            self.bot.handle(self.message(message_id=79,text='Remember me?'))
        with patch('business.time.time',return_value=now+6*3600+60):
            self.bot.flush_business_pending()
        self.assertEqual(len(self.sent()),2)
        self.assertTrue(any(m['content']=='My name is Alex' for m in self.ai.calls[-1]))

    def test_admin_can_retest_delay_without_deleting_history(self):
        self.approve()
        self.s.set('business_tester_id','1')
        self.bot.handle(self.message(text='Remember Alex'))
        self.bot.first_reply_delay=60
        self.assertFalse(self.bot.business_admin(2,'/test_new_dialog'))
        self.bot.business_admin(1,'/test_new_dialog')
        self.bot.handle(self.message(message_id=78,text='Hello again'))
        self.assertEqual(len(self.sent()),1)
        self.assertTrue(any(m['content']=='Remember Alex' for m in self.s.context(-1)))
        self.assertEqual(self.s.get('business_retest_user'),'')
    def test_sticker_read_and_answer_only_after_first_minute(self):
        from unittest.mock import patch
        self.bot.first_reply_delay=60
        self.tg.connection['rights']['can_read_messages']=True
        self.approve()
        now=time.time()
        with patch('business.time.time',return_value=now):
            self.bot.handle(self.message(text='',sticker={'emoji':'👋','file_id':'unused'}))
        self.assertFalse(any(m=='readBusinessMessage' for m,_ in self.tg.calls))
        self.assertEqual(self.sent(),[])
        with patch('business.time.time',return_value=now+59): self.bot.flush_business_pending()
        self.assertFalse(any(m=='readBusinessMessage' for m,_ in self.tg.calls))
        with patch('business.time.time',return_value=now+60): self.bot.flush_business_pending()
        reads=[p for m,p in self.tg.calls if m=='readBusinessMessage']
        self.assertEqual(reads,[{'business_connection_id':'connection-1','chat_id':1,'message_id':77}])
        self.assertEqual(len(self.sent()),1)
        self.assertTrue(any('👋' in m['content'] for m in self.ai.calls[0]))
        self.bot.handle(self.message(message_id=78,text='Hello'))
        self.assertEqual(len([m for m,_ in self.tg.calls if m=='readBusinessMessage']),2)
        self.assertEqual(len(self.sent()),2)

    def test_missing_read_right_does_not_block_reply_or_repeat_warning(self):
        self.approve()
        self.bot.handle(self.message())
        self.bot.handle(self.message(message_id=78))
        self.assertEqual(len(self.sent()),2)
        warnings=[p for m,p in self.tg.calls if m=='sendMessage' and 'Нет права' in p.get('text','')]
        self.assertEqual(len(warnings),1)
    def test_stranger_cannot_approve(self):
        self.assertFalse(self.bot.business_admin(2,'/approve_business 900'))
        self.assertIsNone(self.s.get('business_connection'))
    def test_reply_uses_business_identity_and_separate_memory(self):
        self.approve(); self.bot.handle(self.message())
        self.assertEqual(len(self.sent()),1)
        self.assertEqual(self.sent()[0]['business_connection_id'],'connection-1')
        self.assertEqual(len(self.s.context(1)),1)
        self.assertEqual(self.s.context(-1)[-1]['role'],'assistant')
    def test_duplicate_inbound_message_no_double_reply(self):
        self.approve(); self.bot.handle(self.message()); self.bot.handle(self.message())
        self.assertEqual(len(self.sent()),1)
    def test_owner_outgoing_and_other_visitors_ignored_in_closed_test(self):
        self.approve()
        for sender in [900,2]: self.bot.handle(self.message(**{'from':{'id':sender}}))
        self.bot.handle(self.message(sender_business_bot={'id':123,'is_bot':True}))
        self.assertEqual(self.ai.calls,[])
    def test_revoked_rights_and_old_messages_ignored(self):
        self.approve(); self.tg.connection['rights']['can_reply']=False
        self.bot.handle(self.message()); self.assertEqual(self.ai.calls,[])
        self.tg.connection['rights']['can_reply']=True
        self.bot.handle(self.message(date=time.time()-90000)); self.assertEqual(self.ai.calls,[])
    def test_customer_text_cannot_change_admin_settings(self):
        self.approve(); self.bot.handle(self.message(text='/public on'))
        self.assertIsNone(self.s.get('public'))
    def test_disconnect_while_generating_stops_reply(self):
        self.approve(); self.ai.on_reply=lambda:self.s.set('business_connection','')
        self.bot.handle(self.message()); self.assertEqual(self.sent(),[])

    def test_first_reply_delayed_followups_combined_then_immediate(self):
        from unittest.mock import patch
        self.bot.first_reply_delay=60
        self.approve()
        now=time.time()
        with patch('business.time.time',return_value=now):
            self.bot.handle(self.message())
            self.bot.handle(self.message(message_id=78,text='I am Alex'))
            self.bot.flush_business_pending()
        self.assertEqual(self.sent(),[])
        with patch('business.time.time',return_value=now+59):
            self.bot.flush_business_pending()
        self.assertEqual(self.sent(),[])
        with patch('business.time.time',return_value=now+61):
            self.bot.flush_business_pending()
            self.assertEqual(len(self.sent()),1)
            self.assertTrue(any(m['content']=='I am Alex' for m in self.ai.calls[-1]))
            self.bot.handle(self.message(message_id=79,text='How are you?'))
        self.assertEqual(len(self.sent()),2)

    def test_first_reply_deadline_survives_new_worker(self):
        from unittest.mock import patch
        self.bot.first_reply_delay=60
        self.approve()
        now=time.time()
        with patch('business.time.time',return_value=now): self.bot.handle(self.message())
        worker=Bot(self.tg,self.s,self.ai,1)
        with patch('business.time.time',return_value=now+61): worker.flush_business_pending()
        self.assertEqual(len(self.sent()),1)

if __name__=='__main__': unittest.main()

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
        self.bot.save_business_connection(self.tg.connection,notify=False)
    def tearDown(self): self.s.db.close(); self.temp.cleanup()
    def message(self,**changes):
        msg={'business_connection_id':'connection-1','message_id':77,'from':{'id':1},
            'chat':{'type':'private','id':1},'date':time.time(),'text':'Hi Mila'}
        msg.update(changes)
        return {'business_message':msg}
    def approve(self): self.bot.business_admin(1,'/approve_business 900')
    def sent(self): return [p for m,p in self.tg.calls if m=='sendMessage' and p.get('business_connection_id')]
    def test_no_processing_before_admin_approval(self):
        self.bot.handle(self.message()); self.assertEqual(self.ai.calls,[])
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

if __name__=='__main__': unittest.main()

import tempfile
import unittest
from pathlib import Path
from bot import Bot, Store, LocalAI


class TG:
    def __init__(self): self.calls=[]; self.fail_photo=False
    def call(self,method,**kw):
        self.calls.append((method,kw))
        if method=='sendPhoto' and self.fail_photo: raise RuntimeError('network')
        return True
    def send(self,user,text,**kw): return self.call('sendMessage',chat_id=user,text=text,**kw)

class AI:
    def __init__(self): self.messages=None
    def reply(self,messages): self.messages=messages; return 'Привет!'

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'db.sqlite3'
        self.s=Store(self.path); self.t=TG(); self.ai=AI()
        self.b=Bot(self.t,self.s,self.ai,1,[2])
        with self.s.db:
            self.s.db.execute("INSERT INTO products VALUES(1,'Photo',100,'file1',1)")
    def tearDown(self): self.s.db.close(); self.temp.cleanup()
    def msg(self,user,text):
        return {'message':{'chat':{'type':'private','id':user},'from':{'id':user},'text':text}}
    def order(self):
        self.s.set('sales','on'); self.b.offer(2,1)
        return self.s.db.execute('SELECT * FROM orders').fetchone()['payload']
    def pay(self,payload,**kw):
        return dict(invoice_payload=payload,currency='XTR',total_amount=100,telegram_payment_charge_id='charge1',**kw)
    def test_start_is_english_conversation_without_command_dump(self):
        self.b.handle(self.msg(2,'/start'))
        text=self.t.calls[-1][1]['text']
        self.assertIn("Hey, I'm Mila",text)
        self.assertNotIn('/remember',text)
        self.assertNotIn('AI',text)
        self.assertFalse(any('а' <= c.lower() <= 'я' for c in text))
    def test_free_text_uses_prior_conversation_without_commands(self):
        self.b.handle(self.msg(2,'My name is Alex'))
        self.b.handle(self.msg(2,'What did I just tell you?'))
        self.assertTrue(any(m['content']=='My name is Alex' for m in self.ai.messages))
        self.assertIn('Always reply in natural English',self.ai.messages[0]['content'])
    def test_default_no_sales(self):
        self.b.offer(2,1); self.assertFalse(any(m=='sendInvoice' for m,_ in self.t.calls))
    def test_admin_not_first_stranger(self):
        self.b.handle(self.msg(99,'/sales on')); self.assertFalse(self.b.sales())
        self.b.handle(self.msg(1,'/sales on')); self.assertTrue(self.b.sales())
    def test_private_test(self):
        self.b.handle(self.msg(99,'hi')); self.assertIsNone(self.ai.messages)
    def test_character_is_admin_only_persistent_and_keeps_history(self):
        self.s.append(2,'user','My name is Alex')
        self.b.handle(self.msg(2,'/character Change the shared persona'))
        self.assertIsNone(self.s.get('character'))
        self.b.handle(self.msg(1,'/character Warm, witty, concise.'))
        other=Store(self.path)
        prompt=other.context(-2)[0]['content']
        self.assertIn('Warm, witty, concise.',prompt)
        self.assertIn('be truthful',prompt)
        self.assertTrue(any(m['content']=='My name is Alex' for m in other.context(2)))
        other.db.close()
    def test_memory_isolated_persistent_and_forget(self):
        self.s.remember(2,'My cat is Luna'); self.s.append(2,'user','hello')
        other=Store(self.path)
        self.assertIn('Luna',other.context(2)[0]['content'])
        self.assertNotIn('Luna',other.context(3)[0]['content'])
        other.forget(2); self.assertEqual(len(other.context(2)),1)
        self.assertNotIn('Luna',other.context(2)[0]['content']); other.db.close()
    def test_chat_real_provider_interface(self):
        self.b.handle(self.msg(2,'Hello')); self.assertEqual(self.ai.messages[-1]['content'],'Hello')
        self.assertEqual(self.s.context(2)[-1]['role'],'assistant')
    def test_checkout_validates_user_amount_currency(self):
        payload=self.order()
        q={'id':'q','from':{'id':2},'invoice_payload':payload,'total_amount':100,'currency':'XTR'}
        self.b.checkout(q); self.assertTrue(self.t.calls[-1][1]['ok'])
        for key,value in [('total_amount',1),('currency','USD'),('from',{'id':3})]:
            wrong=dict(q); wrong[key]=value; self.b.checkout(wrong)
            self.assertFalse(self.t.calls[-1][1]['ok'])
        self.assertFalse(any(m=='sendPhoto' for m,_ in self.t.calls))
    def test_duplicate_payment_delivers_once(self):
        payload=self.order(); self.b.payment(2,self.pay(payload)); self.b.payment(2,self.pay(payload))
        self.assertEqual(sum(m=='sendPhoto' for m,_ in self.t.calls),1)
    def test_wrong_payment_no_delivery(self):
        payload=self.order(); payment=self.pay(payload); payment['total_amount']=1
        self.b.payment(2,payment); self.assertFalse(any(m=='sendPhoto' for m,_ in self.t.calls))
    def test_payment_snapshot_survives_catalog_change(self):
        payload=self.order()
        with self.s.db: self.s.db.execute("UPDATE products SET price=200,file_id='new',active=0")
        self.b.payment(2,self.pay(payload))
        photo=next(p for m,p in self.t.calls if m=='sendPhoto'); self.assertEqual(photo['photo'],'file1')
    def test_failed_delivery_retry(self):
        payload=self.order(); self.t.fail_photo=True
        with self.assertRaises(RuntimeError): self.b.payment(2,self.pay(payload))
        self.assertEqual(self.s.db.execute('SELECT status FROM orders').fetchone()[0],'paid')
        self.t.fail_photo=False; self.b.deliver(payload)
        self.assertEqual(self.s.db.execute('SELECT status FROM orders').fetchone()[0],'delivered')
    def test_paid_external_provider_blocked(self):
        for url in ['https://api.openai.com','http://example.com','http://127.0.0.1@evil.com']:
            with self.assertRaises(ValueError): LocalAI(url,'model')
    def test_group_ignored(self):
        msg=self.msg(1,'/sales on'); msg['message']['chat']['type']='group'
        self.b.handle(msg); self.assertFalse(self.b.sales())

if __name__=='__main__': unittest.main()

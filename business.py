"""Approved business-account text replies; no sales or operator commands in customer chats."""
import time
import json


class BusinessMixin:
    def init_business(self):
        self.s.db.executescript('''
        CREATE TABLE IF NOT EXISTS business_connections(
            id TEXT PRIMARY KEY,owner INTEGER NOT NULL,username TEXT,enabled INTEGER,can_reply INTEGER);
        CREATE TABLE IF NOT EXISTS business_seen(connection TEXT,message INTEGER,
            PRIMARY KEY(connection,message));
        ''')
        self.s.db.execute('''
        CREATE TABLE IF NOT EXISTS business_first_reply(
            connection TEXT,user INTEGER,due REAL,payload TEXT,status TEXT,
            PRIMARY KEY(connection,user))
        ''')
        self.s.db.commit()

    def save_business_connection(self, connection, notify=True):
        owner = connection['user']['id']
        with self.s.db:
            self.s.db.execute('INSERT OR REPLACE INTO business_connections VALUES(?,?,?,?,?)',
                (connection['id'],owner,connection['user'].get('username',''),
                 int(connection.get('is_enabled',False)),int(connection.get('rights',{}).get('can_reply',False))))
        if notify:
            username = connection['user'].get('username','без username')
            self.tg.send(self.admin,f'Подключение аккаунта: @{username}, ID {owner}.\n'
                f"Активно: {connection.get('is_enabled',False)}. "
                f"Право ответа: {connection.get('rights',{}).get('can_reply',False)}.\n"
                f'Если это аккаунт Милы, подтверди: /approve_business {owner}\n'
                'До подтверждения автоответы не включаются. Закрытый тест: ответы только администратору.')

    def business_admin(self, user, text):
        if user != self.admin:
            return False
        cmd, _, arg = text.partition(' ')
        if cmd == '/business':
            rows = self.s.db.execute('SELECT owner,username,enabled,can_reply FROM business_connections').fetchall()
            self.tg.send(user,'\n'.join(f"@{r['username']} · ID {r['owner']} · active={r['enabled']} · reply={r['can_reply']}" for r in rows)
                or 'Подключений пока нет. Подключи бота в настройках аккаунта Милы.')
            return True
        if cmd == '/business_off':
            self.s.set('business_connection','')
            self.tg.send(user,'Ответы от имени аккаунта выключены.')
            return True
        if cmd != '/approve_business':
            return False
        if not arg.isdigit():
            self.tg.send(user,'Используй /approve_business ID из уведомления о подключении.')
            return True
        rows = self.s.db.execute('SELECT * FROM business_connections WHERE owner=? AND enabled=1',(int(arg),)).fetchall()
        if len(rows)!=1:
            self.tg.send(user,'Нужно одно активное подключение этого аккаунта. Проверь /business.')
            return True
        connection = self.tg.call('getBusinessConnection',business_connection_id=rows[0]['id'])
        if connection['user']['id']!=int(arg) or not connection.get('is_enabled') or not connection.get('rights',{}).get('can_reply'):
            self.tg.send(user,'Нет активного подключения с правом ответа. Проверь настройки Telegram.')
            return True
        self.save_business_connection(connection,notify=False)
        self.s.set('business_owner',arg)
        self.s.set('business_connection',connection['id'])
        self.tg.send(user,'Аккаунт подтверждён. При public=off отвечаю только тебе. Напиши обычное Hi в личку аккаунта Милы с твоего админского аккаунта.')
        return True

    def flush_business_pending(self):
        rows = self.s.db.execute("SELECT payload FROM business_first_reply WHERE status='pending' AND due<=?",(time.time(),)).fetchall()
        for row in rows:
            self.handle_business_message(json.loads(row['payload']), scheduled=True)

    def handle_business_message(self, message, scheduled=False):
        cid = message.get('business_connection_id')
        if not cid or cid != self.s.get('business_connection',''):
            return
        if message.get('chat',{}).get('type')!='private':
            return
        sender = message.get('from',{})
        user = sender.get('id')
        if not user or sender.get('is_bot') or message.get('sender_business_bot'):
            return
        owner = int(self.s.get('business_owner','0'))
        if user==owner or not self.allowed(user):
            return
        if time.time()-message.get('date',0)>86400:
            return
        text = message.get('text','').strip()
        if not text or len(text)>4000:
            return
        # Revalidate current rights before reading conversation memory or generating a reply.
        connection = self.tg.call('getBusinessConnection',business_connection_id=cid)
        if connection['user']['id']!=owner or not connection.get('is_enabled') or not connection.get('rights',{}).get('can_reply'):
            return
        memory_user = -user
        if not scheduled:
            with self.s.db:
                inserted = self.s.db.execute('INSERT OR IGNORE INTO business_seen VALUES(?,?)',(cid,message['message_id'])).rowcount
            if not inserted or not self.s.quota(user,self.daily_limit):
                return
            if text == '/forget':
                self.s.forget(memory_user)
                with self.s.db:
                    self.s.db.execute("DELETE FROM business_first_reply WHERE connection=? AND user=? AND status='pending'",(cid,user))
                self.tg.send(message['chat']['id'],'Your chat history has been cleared.',business_connection_id=cid)
                return
            previous_reply = self.s.db.execute("SELECT 1 FROM messages WHERE user=? AND role='assistant' LIMIT 1",(memory_user,)).fetchone()
            self.s.append(memory_user,'user',text)
            first = self.s.db.execute('SELECT * FROM business_first_reply WHERE connection=? AND user=?',(cid,user)).fetchone()
            if first and first['status']=='pending':
                # Keep the original deadline; retain each incoming message in the context.
                with self.s.db:
                    self.s.db.execute('UPDATE business_first_reply SET payload=? WHERE connection=? AND user=?',(json.dumps(message),cid,user))
                return
            delay = getattr(self,'first_reply_delay',60)
            if not first and not previous_reply and delay>0:
                with self.s.db:
                    self.s.db.execute("INSERT INTO business_first_reply VALUES(?,?,?,?,'pending')",(cid,user,time.time()+delay,json.dumps(message)))
                return
        else:
            # A restart reloads pending work from SQLite; retry after failures, without a busy loop.
            with self.s.db:
                changed = self.s.db.execute("UPDATE business_first_reply SET due=? WHERE connection=? AND user=? AND status='pending' AND due<=?",(time.time()+30,cid,user,time.time())).rowcount
            if not changed:
                return
        try:
            reply = self.ai.reply(self.s.context(memory_user))
        except Exception:
            self.tg.send(self.admin,'Локальная модель не ответила в личке Милы. Проверь сервис mila-vey-ai.')
            return
        # Re-check approval after slow inference, in case the owner paused replies.
        if cid != self.s.get('business_connection',''):
            return
        connection = self.tg.call('getBusinessConnection',business_connection_id=cid)
        if not connection.get('is_enabled') or not connection.get('rights',{}).get('can_reply') or connection['user']['id']!=owner:
            return
        self.tg.send(message['chat']['id'],reply,business_connection_id=cid)
        self.s.append(memory_user,'assistant',reply)
        with self.s.db:
            self.s.db.execute("UPDATE business_first_reply SET status='done',payload='' WHERE connection=? AND user=?",(cid,user))

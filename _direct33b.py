import json
from backend.database.base import SessionLocal
from backend.services import character_service
from backend.models.character import AICharacter, Conversation, Text

db = SessionLocal()
u = db.query(AICharacter).first().user_id
ch = AICharacter(name='e2e_direct_y', user_id=u, system_prompt='x')
db.add(ch); db.commit(); db.refresh(ch)
cid = ch.id
conv = Conversation(character_id=cid, user_id=u, title='t', texts=json.dumps([]), text_count=0)
db.add(conv); db.commit(); db.refresh(conv)
db.add(Text(conversation_id=conv.id, role='user', content='hi'))
db.commit()
res = character_service.delete_character(str(cid), user_id=u)
print("delete res:", res)
db2 = SessionLocal()
nconv = db2.query(Conversation).filter(Conversation.character_id == cid).count()
ntext = db2.query(Text).join(Conversation, Text.conversation_id == Conversation.id).filter(Conversation.character_id == cid).count()
print("conv after:", nconv, "text after:", ntext)
print("CASCADE_OK" if (nconv == 0 and ntext == 0) else "CASCADE_FAIL")
db.close(); db2.close()

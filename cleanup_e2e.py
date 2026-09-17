# -*- coding: utf-8 -*-
"""清理 E2E 产生的测试数据：users(username LIKE 'e2e_%') 及其级联的
ai_characters / conversations / texts / user_session。不删除任何真实数据，不清空整表。"""
import os, sys
from sqlalchemy import create_engine, text

ROOT = r"C:\Users\Administrator\Desktop\my_ai_assistant"
DB = os.path.join(ROOT, "data", "ai_assistant.db")
if not os.path.exists(DB):
    # 回退：开发库路径
    DB = os.path.join(ROOT, "my_ai_assistant.db")
print("DB path:", DB, "exists=", os.path.exists(DB))

engine = create_engine(f"sqlite:///{DB}")
with engine.begin() as conn:
    uids = [r[0] for r in conn.execute(text("SELECT id FROM users WHERE username LIKE 'e2e_%'")).all()]
    print("e2e user ids:", uids)
    if not uids:
        print("无测试数据可清理。")
        sys.exit(0)
    ph = "(" + ",".join(str(i) for i in uids) + ")"

    cids = [r[0] for r in conn.execute(text(f"SELECT id FROM ai_characters WHERE user_id IN {ph}")).all()]
    print("e2e character ids:", cids)
    conv_ph = "(-1)"
    if cids:
        conv_ph = "(" + ",".join(str(i) for i in cids) + ")"
        conv_ids = [r[0] for r in conn.execute(text(f"SELECT id FROM conversations WHERE character_id IN {conv_ph}")).all()]
        print("e2e conversation ids:", conv_ids)
        if conv_ids:
            txt_ph = "(" + ",".join(str(i) for i in conv_ids) + ")"
            n_texts = conn.execute(text(f"DELETE FROM texts WHERE conversation_id IN {txt_ph}")).rowcount
            print("deleted texts:", n_texts)
        n_conv = conn.execute(text(f"DELETE FROM conversations WHERE character_id IN {conv_ph}")).rowcount
        print("deleted conversations:", n_conv)
        n_char = conn.execute(text(f"DELETE FROM ai_characters WHERE user_id IN {ph}")).rowcount
        print("deleted ai_characters:", n_char)
    n_sess = conn.execute(text(f"DELETE FROM user_session WHERE user_id IN {ph}")).rowcount
    print("deleted user_sessions:", n_sess)
    n_users = conn.execute(text(f"DELETE FROM users WHERE username LIKE 'e2e_%'")).rowcount
    print("deleted users:", n_users)
    print("清理完成。")

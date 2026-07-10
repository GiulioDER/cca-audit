from flask import request


def get_user(cursor):
    uid = request.args.get("id")
    cursor.execute("SELECT * FROM users WHERE id = %s", (uid,))

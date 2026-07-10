from flask import request


def get_user(cursor):
    uid = request.args.get("id")
    query = "SELECT * FROM users WHERE id = " + uid
    cursor.execute(query)

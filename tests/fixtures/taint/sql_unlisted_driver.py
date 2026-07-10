from flask import request


def get_user(con):
    uid = request.args.get("id")
    return con.sql("SELECT * FROM users WHERE id = " + uid)

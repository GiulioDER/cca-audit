from flask import request


def get_user(session, Model):
    uid = request.args.get("id")
    return session.query(Model).filter(Model.id == uid).all()

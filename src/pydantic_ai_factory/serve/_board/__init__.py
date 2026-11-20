"""The board's web process, which needs the ``[web]`` extra.

``app`` is the only module here that imports FastAPI or Starlette. The rest build the
board from the store and the dispatcher: ``views`` holds what the API sends and takes,
``cards`` the rules that place each card, ``reading`` what is read to card it,
``pages`` the board and its columns page by page, ``history`` a run's track and
history, ``detail`` one card in full, and ``answers`` a person's answer.
"""

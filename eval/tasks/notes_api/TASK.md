# Task: notes_api

Write an HTTP server for a small notes service: a JSON API plus one HTML page.

## Running

* Listen on `127.0.0.1` at the port given by the `PORT` environment variable.
* Store data in files inside the directory given by the `DATA_DIR` environment variable (it exists and starts
  empty; any file format or a SQLite database is fine). Notes must survive a restart of the server: stopping
  it and starting it again with the same `DATA_DIR` shows the same notes, and ids keep counting from where
  they were.

## Notes

A note is returned as this JSON object:

```json
{"id": 1, "title": "Groceries", "body": "milk, eggs", "tags": ["home", "todo"]}
```

* `id`: a whole number. The first note gets 1, each new note the next number. Ids of deleted notes are never
  reused, also not after a restart.
* `title`: a string that is not empty after trimming spaces, at most 100 characters. It is stored trimmed.
* `body`: a string (may be empty).
* `tags`: an array of strings. Each tag is 1 to 20 characters from `a-z`, `0-9` and `-`. Duplicates are
  removed, keeping the first occurrence and the order.

## Endpoints

| Request | Response |
|---|---|
| `POST /notes` with a JSON body `{"title": ..., "body": ..., "tags": [...]}` (`body` defaults to `""`, `tags` to `[]`) | `201` and the created note |
| `GET /notes` | `200` and an array of all notes, by ascending id |
| `GET /notes?tag=T` | only the notes that have tag `T` |
| `GET /notes?q=WORD` | only the notes whose title or body contains `WORD`, ignoring ASCII case |
| `GET /notes?tag=T&q=WORD` | both filters |
| `GET /notes/ID` | `200` and the note |
| `PATCH /notes/ID` with a JSON body holding any of `title`, `body`, `tags` | `200` and the updated note; fields not given keep their value |
| `DELETE /notes/ID` | `204` with an empty body |
| `GET /` | `200`, an HTML page (`Content-Type: text/html`) listing each note's title in an `<li>` element |

Errors answer with a JSON object holding an `error` string, `{"error": "title is required"}`:

* `400` when the body is not valid JSON, is not an object, or a field breaks the rules above (a missing or empty
  title on `POST`; a title, body or tags of the wrong type; an invalid tag). Nothing is stored or changed.
* `404` when the note does not exist (including an `ID` that is not a whole number), and for any path not
  listed above.

All JSON responses use `Content-Type: application/json`.

The HTML page must show titles as text: a title like `<b>hi</b>` appears as the characters `<b>hi</b>`, not as
markup.

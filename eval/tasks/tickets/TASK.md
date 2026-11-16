# Task: tickets

This folder holds an existing program: a small box office that reads commands from standard input and prints
one result per command. Its source is in this folder and `examples/` has sample inputs with the output the
program gives today. Read the code first, then implement the ticket below. Everything the ticket does not
change must keep working exactly as it does now (same commands, same messages, same output format).

---

**Ticket #142: refunds pay back the wrong amount; add a waitlist**

*Reported by the box office team.*

**Bug.** When we change an event's price after some people have bought tickets, refunds pay back the *new*
price instead of what the person actually paid, and the revenue in `report` ends up wrong. A refund must pay
back exactly what that person paid for their tickets to that event (they may have bought several times at
different prices), and revenue must go down by that amount.

**Waitlist.** Today a `buy` that does not fit prints `error: sold out: ID`. Instead:

* The request joins the event's waitlist and the program prints
  `waitlist: PERSON wants QTY for ID (position P)`, where P is its 1-based position in that event's waitlist.
* A person can only wait once per event. If PERSON is already on the waitlist, print
  `error: already waiting: PERSON` and change nothing.
* Whenever seats become free (after a refund, or after the new `capacity` command below), go through the
  waitlist from the front. Every request that now fits is filled at the event's **current** price, printing
  the same line a normal purchase prints (`ok: PERSON bought QTY for ID (TOTAL)`), and leaves the waitlist. A
  request that does not fit stays where it is, and later requests can still be filled. These lines come
  right after the line of the command that freed the seats, in waitlist order.
* `refund ID PERSON` for a person who holds no tickets for that event but is on its waitlist takes them off
  the waitlist and prints `ok: left waitlist: PERSON`.

**New command.** `capacity ID N` sets the event's capacity to N and prints `ok: capacity ID N`. If N is less
than the number of tickets sold, print `error: capacity below sold: ID` and change nothing. With the wrong
number of arguments print `error: usage: capacity ID N`; other errors (unknown event, bad number) use the same
messages as the existing commands.

**Report.** When an event's waitlist is not empty, its `report` line ends with `, waiting K`, where K is the
number of requests on the waitlist: `gala: 3/3 sold, revenue 30.00, waiting 2`.

---

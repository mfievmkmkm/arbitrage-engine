def annotate(row,book_fresh,fees_verified,funding_known):
 x=dict(row);x.update(book_fresh=bool(book_fresh),fees_verified=bool(fees_verified),funding_known=bool(funding_known));x["paper_allowed"]=all((x["book_fresh"],x["fees_verified"],x["funding_known"]));return x

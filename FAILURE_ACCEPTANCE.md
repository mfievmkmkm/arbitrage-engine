# Micro-live failure acceptance matrix

The first live release is fail-closed. These scenarios must never create a second blind order or silently forget exposure:

1. stale long book
2. stale short book
3. funding unknown
4. adverse funding imminent
5. fee unverified
6. insufficient long margin
7. insufficient short margin
8. insufficient long depth
9. insufficient short depth
10. symbol inactive
11. contract metadata invalid
12. spread shock during entry
13. long submit timeout
14. short submit timeout
15. accepted order with lost response
16. partial long fill
17. partial short fill
18. actual NET below threshold
19. private hedge mismatch
20. flipped private exposure
21. stale private snapshot
22. crash after intent persistence
23. crash after one/both entry fills
24. crash while HEDGED
25. crash after exit intents
26. crash after exit fills
27. partial close
28. close recovery failure
29. residual private exposure after close
30. repeated API errors
31. venue-specific error streak
32. daily realized loss stop
33. equity drawdown stop
34. operator STOP
35. global/venue/pair kill switch
36. pair cooldown
37. attempted capital scale-up without verified history

Expected behavior: block, reconcile, recover, or halt explicitly. Never guess.

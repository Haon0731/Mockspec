(set-logic ALL)
(declare-fun response () Bool)
(assert (not response))
(check-sat)

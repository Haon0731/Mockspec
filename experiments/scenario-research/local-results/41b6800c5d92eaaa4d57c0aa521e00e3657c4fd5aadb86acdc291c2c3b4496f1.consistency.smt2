(set-logic ALL)
(declare-fun response () Bool)
(assert (not (not response)))
(check-sat)

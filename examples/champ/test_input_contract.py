import unittest
from input_contract import from_observations, parse_answer, STOP, NORMAL


class ContractTests(unittest.TestCase):
    def test_no_command_duration_leak(self):
        # Identical observed motion/anchor with different wall positions in a rollout.
        a=['anchor']+['x']*39+list(range(22))
        b=['anchor']+['x']*139+list(range(22))
        ra,_=from_observations('BinFill','fill','pick cube',a,0,'wrist')
        rb,_=from_observations('BinFill','fill','pick cube',b,0,'wrist')
        self.assertEqual(ra,rb)

    def test_command_reference_and_causal_window(self):
        row,ids=from_observations('StopCube','stop','press button',list(range(60)),45,'wrist')
        self.assertEqual(ids,[38,41,44,47,50,53,56,59])
        self.assertEqual(row['images'][-2:],[45,'wrist'])
        self.assertEqual(row['messages'][0]['content'],STOP)
        self.assertNotEqual(STOP,NORMAL)

    def test_output_schema(self):
        self.assertTrue(parse_answer('true'))
        self.assertFalse(parse_answer('false'))
        for x in ('True','{"is_transition":true}','true because yes',''):
            with self.assertRaises(ValueError):parse_answer(x)


if __name__=='__main__':unittest.main()

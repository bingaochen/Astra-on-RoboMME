"""Causal ten-image boolean monitor, without command duration or frame offsets."""
COMMON = ('You monitor the current grounded subgoal using visual observations. '
          'Judge the specified action and target, not a previous action. '
          'An action may fail or require repeated attempts; do not assume success from typical duration. '
          'The recent eight front-camera images are chronological and sampled three environment steps apart. '
          'The command-start front image identifies this command instance; it is not evidence of completion. '
          'The final image is the current wrist-camera view. ')
NORMAL = COMMON + ('Return true only when the current grounded subgoal has completed by the current observation. '
                   'Otherwise return false. Output exactly one lowercase word: true or false. No explanation or JSON.')
STOP = COMMON + ('For StopCube, use the current grounded subgoal to select the rule. '
                 'For a waiting subgoal (remain static), return true when the moving cube is on the incoming leg '
                 'toward the target, approximately 16 to 32 environment steps before reaching it, on every pass '
                 'in either direction. Return false outside that interval, while moving away, or after it has stopped. '
                 'Do not count passes or decide whether to press the button; the high-level planner does that. '
                 'For a preparation or press-button subgoal, return true only when that specified action is complete; '
                 'approaching the target alone does not complete these actions. Otherwise return false. '
                 'Output exactly one lowercase word: true or false. No explanation or JSON.')


def build_input(task, goal, subgoal, recent, anchor, wrist):
    assert len(recent) == 8
    text = (f'Task name: {task}\nTask instruction: {goal}\nCurrent grounded subgoal: {subgoal}\n'
            'Recent front-camera observations, oldest to current:\n')
    text += '\n'.join(f'Recent image {i}: <image>' for i in range(8))
    text += '\nCommand-start front reference: <image>\nCurrent wrist-camera observation: <image>'
    return {'messages': [{'role': 'system', 'content': STOP if task == 'StopCube' else NORMAL},
                         {'role': 'user', 'content': text}], 'images': list(recent) + [anchor, wrist]}


def from_observations(task, goal, subgoal, front_history, command_start, wrist, execution_start=0):
    now = len(front_history) - 1
    assert execution_start <= command_start <= now
    ids = [max(execution_start, now - 3 * (7 - i)) for i in range(8)]
    return build_input(task, goal, subgoal, [front_history[t] for t in ids],
                       front_history[command_start], wrist), ids


def parse_answer(text):
    value = text.strip()
    if value not in ('true', 'false'):
        raise ValueError('Expected exactly true or false')
    return value == 'true'

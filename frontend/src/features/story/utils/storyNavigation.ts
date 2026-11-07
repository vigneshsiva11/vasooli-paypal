export interface StorySlideInfo {
  index: number;
  act: 1 | 2 | 3;
  actTitle: string;
  slideTitle: string;
}

export const STORY_SLIDES: StorySlideInfo[] = [
  // Act 1: "Say the rules"
  { index: 0, act: 1, actTitle: 'Say the rules', slideTitle: 'Plain-English Rule Proposal' },
  { index: 1, act: 1, actTitle: 'Say the rules', slideTitle: 'Parameter Grounding & Verbatim Source Phrases' },
  { index: 2, act: 1, actTitle: 'Say the rules', slideTitle: 'Hard Safety Caps & Prospective Fingerprint' },

  // Act 2: "Watch it work"
  { index: 3, act: 2, actTitle: 'Watch it work', slideTitle: 'Replay: Step 1 – Customer Onboard' },
  { index: 4, act: 2, actTitle: 'Watch it work', slideTitle: 'Replay: Step 2 – Renewal Attempt Declined' },
  { index: 5, act: 2, actTitle: 'Watch it work', slideTitle: 'Replay: Step 3 – Decline Webhook Ingested' },
  { index: 6, act: 2, actTitle: 'Watch it work', slideTitle: 'Replay: Step 4 – Vasooli Pipeline & Policy' },
  { index: 7, act: 2, actTitle: 'Watch it work', slideTitle: 'Replay: Step 5 – Simulated Clock Cooldown' },
  { index: 8, act: 2, actTitle: 'Watch it work', slideTitle: 'Replay: Step 6 – Payment Retry Executed' },
  { index: 9, act: 2, actTitle: 'Watch it work', slideTitle: 'Replay: Step 7 – Verification & Recovery' },
  { index: 10, act: 2, actTitle: 'Watch it work', slideTitle: 'Replay: Final Verified Amount & Why Card' },

  // Act 3: "Try to break it"
  { index: 11, act: 3, actTitle: 'Try to break it', slideTitle: 'Scenario 1: Retry on a Do-Not-Retry Decline (9500)' },
  { index: 12, act: 3, actTitle: 'Try to break it', slideTitle: 'Scenario 2: An Opted-Out Contact' },
  { index: 13, act: 3, actTitle: 'Try to break it', slideTitle: 'Scenario 3: A $1,500 Spend Above Safe Ceiling' },
  { index: 14, act: 3, actTitle: 'Try to break it', slideTitle: 'Audit Verification & Ready for Live' },
];

export const TOTAL_SLIDES = STORY_SLIDES.length; // 15 slides * 12s = 180s (3 minutes)

export interface StoryNavState {
  slideIndex: number;
  isPlaying: boolean;
}

export type StoryNavAction =
  | { type: 'NEXT_SLIDE' }
  | { type: 'PREV_SLIDE' }
  | { type: 'TOGGLE_PLAY' }
  | { type: 'SET_PLAYING'; isPlaying: boolean }
  | { type: 'GO_TO_SLIDE'; index: number }
  | { type: 'GO_TO_ACT'; act: 1 | 2 | 3 };

export function storyNavReducer(state: StoryNavState, action: StoryNavAction): StoryNavState {
  switch (action.type) {
    case 'NEXT_SLIDE':
      return {
        ...state,
        slideIndex: Math.min(state.slideIndex + 1, TOTAL_SLIDES - 1),
      };
    case 'PREV_SLIDE':
      return {
        ...state,
        slideIndex: Math.max(state.slideIndex - 1, 0),
      };
    case 'TOGGLE_PLAY':
      return {
        ...state,
        isPlaying: !state.isPlaying,
      };
    case 'SET_PLAYING':
      return {
        ...state,
        isPlaying: action.isPlaying,
      };
    case 'GO_TO_SLIDE':
      return {
        ...state,
        slideIndex: Math.max(0, Math.min(action.index, TOTAL_SLIDES - 1)),
      };
    case 'GO_TO_ACT': {
      const targetIndex = STORY_SLIDES.findIndex((s) => s.act === action.act);
      return {
        ...state,
        slideIndex: targetIndex >= 0 ? targetIndex : 0,
      };
    }
    default:
      return state;
  }
}

/**
 * Handles keyboard events according to requirements:
 * - ArrowRight / ArrowDown: Next slide
 * - ArrowLeft / ArrowUp: Previous slide
 * - Space: Toggle pause/play
 */
export function handleStoryKeyboardEvent(
  e: { key: string; code?: string; preventDefault?: () => void },
  dispatch: (action: StoryNavAction) => void
): boolean {
  if (e.code === 'Space' || e.key === ' ' || e.key === 'Spacebar') {
    if (e.preventDefault) e.preventDefault();
    dispatch({ type: 'TOGGLE_PLAY' });
    return true;
  }

  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
    if (e.preventDefault) e.preventDefault();
    dispatch({ type: 'NEXT_SLIDE' });
    return true;
  }

  if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
    if (e.preventDefault) e.preventDefault();
    dispatch({ type: 'PREV_SLIDE' });
    return true;
  }

  return false;
}


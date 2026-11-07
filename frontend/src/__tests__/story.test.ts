import { describe, it, expect, vi } from 'vitest';
import {
  COMPRESSED_TIME_LABEL,
  getRulePreviewCaption,
  getReplayStepCaption,
  getReplayVerificationCaption,
  getScenarioAttemptCaption,
  hasUnfilledPlaceholder,
} from '../features/story/utils/storyCaptions';
import {
  storyNavReducer,
  handleStoryKeyboardEvent,
  TOTAL_SLIDES,
  STORY_SLIDES,
  type StoryNavState,
} from '../features/story/utils/storyNavigation';
import {
  BUNDLED_RULE_PREVIEWS,
  BUNDLED_REPLAY_RUN,
  BUNDLED_SCENARIO_ATTEMPTS,
} from '../features/story/utils/storyData';

describe('Story Mode: Compressed-time label', () => {
  it('contains the exact required wording for sandbox run replay', () => {
    expect(COMPRESSED_TIME_LABEL).toBe(
      'Replay of a real sandbox run. Waiting time compressed. Sandbox, no real money.'
    );
  });

  it('includes key assurances: waiting time compressed and sandbox notice', () => {
    expect(COMPRESSED_TIME_LABEL).toContain('Waiting time compressed');
    expect(COMPRESSED_TIME_LABEL).toContain('Sandbox, no real money.');
  });
});

describe('Story Mode: Caption templates (no placeholder left unfilled)', () => {
  describe('Act 1: Rule preview captions', () => {
    it('formats valid rule proposal strictly from real data fields with no unfilled placeholders', () => {
      const preview = BUNDLED_RULE_PREVIEWS.conservative;
      const caption = getRulePreviewCaption(preview);

      expect(caption).toContain(preview.text);
      expect(caption).toContain(preview.prospective_fingerprint!);
      expect(caption).toContain('valid with prospective fingerprint');
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
      expect(caption).not.toMatch(/undefined|null|NaN|\[object Object\]|\{[a-zA-Z0-9_]+\}/);
    });

    it('formats rejected proposal strictly from real data fields with no unfilled placeholders', () => {
      const preview = BUNDLED_RULE_PREVIEWS.injection;
      const caption = getRulePreviewCaption(preview);

      expect(caption).toContain(preview.text);
      expect(caption).toContain('rejected with 2 safety invariant violations');
      expect(caption).toContain(preview.current_fingerprint);
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
      expect(caption).not.toMatch(/undefined|null|NaN|\[object Object\]|\{[a-zA-Z0-9_]+\}/);
    });
  });

  describe('Act 2: Step-by-step replay captions', () => {
    const steps = BUNDLED_REPLAY_RUN.steps;

    it('has all 7 real replay steps in bundled run', () => {
      expect(steps).toHaveLength(7);
    });

    it('formats step 1 (onboard) with real customer_ref', () => {
      const step = steps[0];
      const caption = getReplayStepCaption(step);
      expect(caption).toContain('Customer demo-001 onboarded');
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
    });

    it('formats step 2 (renewal_attempt) with real order_id and processor code', () => {
      const step = steps[1];
      const caption = getReplayStepCaption(step);
      expect(caption).toContain('6GH69097AN303143V');
      expect(caption).toContain('DECLINED');
      expect(caption).toContain('5120');
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
    });

    it('formats step 3 (decline_ingested) with real event_id and order_id', () => {
      const step = steps[2];
      const caption = getReplayStepCaption(step);
      expect(caption).toContain('pp_4HV420162D295612U');
      expect(caption).toContain('6GH69097AN303143V');
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
    });

    it('formats step 4 (pipeline) with real diagnosis, verdict, and execution ids', () => {
      const step = steps[3];
      const caption = getReplayStepCaption(step);
      expect(caption).toContain('6ae6ec5757392a70552ed259');
      expect(caption).toContain('authorized');
      expect(caption).toContain('6ae6ec5e57392a70552ed25f');
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
    });

    it('formats step 5 (clock) with real advance_hours', () => {
      const step = steps[4];
      const caption = getReplayStepCaption(step);
      expect(caption).toContain('72 hours');
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
    });

    it('formats step 6 (retry_executed) with real gateway_ref and response code', () => {
      const step = steps[5];
      const caption = getReplayStepCaption(step);
      expect(caption).toContain('3LK16598GX422304W');
      expect(caption).toContain('COMPLETED');
      expect(caption).toContain('0000');
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
    });

    it('formats step 7 (verified) with real verification_id and outcome', () => {
      const step = steps[6];
      const caption = getReplayStepCaption(step);
      expect(caption).toContain('6ae6ec6d57392a70552ed262');
      expect(caption).toContain('recovered');
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
    });

    it('verifies that NO replay step leaves any unfilled placeholder', () => {
      steps.forEach((step) => {
        const caption = getReplayStepCaption(step);
        expect(hasUnfilledPlaceholder(caption), `Step ${step.name} left unfilled placeholder: "${caption}"`).toBe(false);
        expect(caption).not.toMatch(/undefined|null|NaN|\[object Object\]|\{[a-zA-Z0-9_]+\}/);
      });
    });

    it('formats final verified amount caption from real numbers with no unfilled placeholders', () => {
      const caption = getReplayVerificationCaption(49.0, 'USD', 'recovered');
      expect(caption).toBe('Replay verified: 49.00 USD recovered with outcome recovered.');
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
      expect(caption).not.toMatch(/undefined|null|NaN|\[object Object\]|\{[a-zA-Z0-9_]+\}/);
    });
  });

  describe('Act 3: Live attack scenario captions', () => {
    it('formats scenario 1 (retry on do-not-retry decline 9500) strictly from real data', () => {
      const attempt = BUNDLED_SCENARIO_ATTEMPTS.retry_on_9500.response;
      const caption = getScenarioAttemptCaption(attempt);

      expect(caption).toContain('Retry on 9500');
      expect(caption).toContain('propose_recovery');
      expect(caption).toContain('BLOCKED');
      expect(caption).toContain('retry_not_advised');
      expect(caption).toContain('decision_is_actionable');
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
      expect(caption).not.toMatch(/undefined|null|NaN|\[object Object\]|\{[a-zA-Z0-9_]+\}/);
    });

    it('formats scenario 2 (opted-out contact) strictly from real data', () => {
      const attempt = BUNDLED_SCENARIO_ATTEMPTS.opted_out_contact.response;
      const caption = getScenarioAttemptCaption(attempt);

      expect(caption).toContain('Opted-out contact');
      expect(caption).toContain('propose_contact');
      expect(caption).toContain('BLOCKED');
      expect(caption).toContain('customer_opted_out');
      expect(caption).toContain('customer_opt_out');
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
      expect(caption).not.toMatch(/undefined|null|NaN|\[object Object\]|\{[a-zA-Z0-9_]+\}/);
    });

    it('formats scenario 3 ($1,500 spend) strictly from real data', () => {
      const attempt = BUNDLED_SCENARIO_ATTEMPTS['1500_without_approval'].response;
      const caption = getScenarioAttemptCaption(attempt);

      expect(caption).toContain('$1,500 without approval');
      expect(caption).toContain('propose_recovery');
      expect(caption).toContain('REQUIRES_MANUAL_REVIEW');
      expect(caption).toContain('amount_never_auto');
      expect(caption).toContain('amount_tier');
      expect(hasUnfilledPlaceholder(caption)).toBe(false);
      expect(caption).not.toMatch(/undefined|null|NaN|\[object Object\]|\{[a-zA-Z0-9_]+\}/);
    });
  });
});

describe('Story Mode: Keyboard controls and navigation', () => {
  const initial: StoryNavState = {
    slideIndex: 0,
    isPlaying: true,
  };

  it('navigates forward with ArrowRight and ArrowDown', () => {
    let s = storyNavReducer(initial, { type: 'NEXT_SLIDE' });
    expect(s.slideIndex).toBe(1);

    s = storyNavReducer(s, { type: 'NEXT_SLIDE' });
    expect(s.slideIndex).toBe(2);
  });

  it('clamps at max slide index', () => {
    let s: StoryNavState = { slideIndex: TOTAL_SLIDES - 1, isPlaying: true };
    s = storyNavReducer(s, { type: 'NEXT_SLIDE' });
    expect(s.slideIndex).toBe(TOTAL_SLIDES - 1);
  });

  it('navigates backward with ArrowLeft and ArrowUp', () => {
    let s: StoryNavState = { slideIndex: 3, isPlaying: true };
    s = storyNavReducer(s, { type: 'PREV_SLIDE' });
    expect(s.slideIndex).toBe(2);

    s = storyNavReducer(s, { type: 'PREV_SLIDE' });
    expect(s.slideIndex).toBe(1);
  });

  it('clamps at minimum slide index (0)', () => {
    let s: StoryNavState = { slideIndex: 0, isPlaying: true };
    s = storyNavReducer(s, { type: 'PREV_SLIDE' });
    expect(s.slideIndex).toBe(0);
  });

  it('toggles pause and play state with Space', () => {
    let s = storyNavReducer(initial, { type: 'TOGGLE_PLAY' });
    expect(s.isPlaying).toBe(false);

    s = storyNavReducer(s, { type: 'TOGGLE_PLAY' });
    expect(s.isPlaying).toBe(true);
  });

  it('jumps directly to Act 1, Act 2, and Act 3', () => {
    let s = storyNavReducer(initial, { type: 'GO_TO_ACT', act: 2 });
    expect(STORY_SLIDES[s.slideIndex].act).toBe(2);

    s = storyNavReducer(s, { type: 'GO_TO_ACT', act: 3 });
    expect(STORY_SLIDES[s.slideIndex].act).toBe(3);

    s = storyNavReducer(s, { type: 'GO_TO_ACT', act: 1 });
    expect(STORY_SLIDES[s.slideIndex].act).toBe(1);
    expect(s.slideIndex).toBe(0);
  });

  it('keyboard handler intercepts Arrow keys and Space correctly', () => {
    const dispatchMock = vi.fn();

    // Space
    const spaceEvent = { key: ' ', code: 'Space', preventDefault: vi.fn() };
    const handledSpace = handleStoryKeyboardEvent(spaceEvent, dispatchMock);
    expect(handledSpace).toBe(true);
    expect(spaceEvent.preventDefault).toHaveBeenCalled();
    expect(dispatchMock).toHaveBeenCalledWith({ type: 'TOGGLE_PLAY' });

    // ArrowRight
    dispatchMock.mockClear();
    const rightEvent = { key: 'ArrowRight', preventDefault: vi.fn() };
    const handledRight = handleStoryKeyboardEvent(rightEvent, dispatchMock);
    expect(handledRight).toBe(true);
    expect(rightEvent.preventDefault).toHaveBeenCalled();
    expect(dispatchMock).toHaveBeenCalledWith({ type: 'NEXT_SLIDE' });

    // ArrowLeft
    dispatchMock.mockClear();
    const leftEvent = { key: 'ArrowLeft', preventDefault: vi.fn() };
    const handledLeft = handleStoryKeyboardEvent(leftEvent, dispatchMock);
    expect(handledLeft).toBe(true);
    expect(leftEvent.preventDefault).toHaveBeenCalled();
    expect(dispatchMock).toHaveBeenCalledWith({ type: 'PREV_SLIDE' });

    // Other keys
    dispatchMock.mockClear();
    const otherEvent = { key: 'KeyA', preventDefault: vi.fn() };
    const handledOther = handleStoryKeyboardEvent(otherEvent, dispatchMock);
    expect(handledOther).toBe(false);
    expect(dispatchMock).not.toHaveBeenCalled();
  });
});

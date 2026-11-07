import React, { useReducer, useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  MessageSquare,
  ExternalLink,
} from 'lucide-react';
import {
  storyNavReducer,
  handleStoryKeyboardEvent,
  STORY_SLIDES,
  TOTAL_SLIDES,
} from '../utils/storyNavigation';
import { StoryProgressBar } from './StoryProgressBar';
import { Act1SayTheRules } from './Act1SayTheRules';
import { Act2WatchItWork } from './Act2WatchItWork';
import { Act3TryToBreakIt } from './Act3TryToBreakIt';
import type { RulePreviewResponse } from '@/features/rules/api/rulesApi';
import {
  getRulePreviewCaption,
} from '../utils/storyCaptions';
import { BUNDLED_RULE_PREVIEWS } from '../utils/storyData';

const SLIDE_DURATION_SECONDS = 12; // 15 slides * 12s = 180s = 3 minutes

export const StoryRoute: React.FC = () => {
  const [navState, dispatch] = useReducer(storyNavReducer, {
    slideIndex: 0,
    isPlaying: true,
  });

  const [currentCaption, setCurrentCaption] = useState<string>('');
  const [timerSecondsRemaining, setTimerSecondsRemaining] = useState<number>(SLIDE_DURATION_SECONDS);

  const currentSlide = STORY_SLIDES[navState.slideIndex] || STORY_SLIDES[0];

  // Global Keyboard Navigation (Arrow keys and Space to pause/play)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      // Don't intercept if focus is inside an input or textarea
      const target = e.target as HTMLElement;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
        return;
      }
      handleStoryKeyboardEvent(e, dispatch);
    };

    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
    };
  }, []);

  // Auto-play timer for a 3-minute screen recording (~180s total)
  useEffect(() => {
    setTimerSecondsRemaining(SLIDE_DURATION_SECONDS);
  }, [navState.slideIndex]);

  useEffect(() => {
    if (!navState.isPlaying) return;

    const interval = setInterval(() => {
      setTimerSecondsRemaining((prev) => {
        if (prev <= 1) {
          if (navState.slideIndex < TOTAL_SLIDES - 1) {
            dispatch({ type: 'NEXT_SLIDE' });
            return SLIDE_DURATION_SECONDS;
          } else {
            dispatch({ type: 'SET_PLAYING', isPlaying: false });
            return 0;
          }
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(interval);
  }, [navState.isPlaying, navState.slideIndex]);

  // Set default initial caption for Act 1
  useEffect(() => {
    if (currentSlide.act === 1 && !currentCaption) {
      setCurrentCaption(getRulePreviewCaption(BUNDLED_RULE_PREVIEWS.conservative));
    }
  }, [currentSlide.act, currentCaption]);

  const handlePreviewLoaded = useCallback((preview: RulePreviewResponse) => {
    setCurrentCaption(getRulePreviewCaption(preview));
  }, []);

  const handleStepCaption = useCallback((caption: string) => {
    setCurrentCaption(caption);
  }, []);

  const handleScenarioCaption = useCallback((caption: string) => {
    setCurrentCaption(caption);
  }, []);

  return (
    <div className="min-h-screen w-full bg-slate-950 text-slate-100 flex flex-col justify-between selection:bg-cyan-500 selection:text-slate-950">
      {/* Top Header & Progress Bar */}
      <div>
        <StoryProgressBar
          slideIndex={navState.slideIndex}
          isPlaying={navState.isPlaying}
          onNext={() => dispatch({ type: 'NEXT_SLIDE' })}
          onPrev={() => dispatch({ type: 'PREV_SLIDE' })}
          onTogglePlay={() => dispatch({ type: 'TOGGLE_PLAY' })}
          onSelectAct={(act) => dispatch({ type: 'GO_TO_ACT', act })}
        />

        {/* Presentation Stage Content */}
        <main className="max-w-7xl mx-auto px-4 sm:px-6 py-6 w-full">
          {currentSlide.act === 1 && (
            <Act1SayTheRules
              slideIndex={navState.slideIndex}
              onPreviewLoaded={handlePreviewLoaded}
            />
          )}

          {currentSlide.act === 2 && (
            <Act2WatchItWork
              slideIndex={navState.slideIndex}
              onStepChanged={handleStepCaption}
            />
          )}

          {currentSlide.act === 3 && (
            <Act3TryToBreakIt
              slideIndex={navState.slideIndex}
              onScenarioExecuted={handleScenarioCaption}
            />
          )}
        </main>
      </div>

      {/* Persistent Bottom Presentation Ribbon with LARGE CAPTION */}
      <footer className="w-full bg-slate-900/95 border-t border-slate-800 backdrop-blur-md sticky bottom-0 z-40 p-4 shadow-2xl">
        <div className="max-w-7xl mx-auto flex flex-col gap-3">
          {/* Large Caption Banner */}
          <div className="flex items-start gap-3">
            <div className="rounded-lg bg-cyan-500/10 border border-cyan-400/30 p-2 text-cyan-400 shrink-0 mt-0.5">
              <MessageSquare className="h-5 w-5" />
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-bold uppercase tracking-wider text-cyan-400">
                  Data-Grounded Narrative Caption
                </span>
                <span className="text-[10px] text-slate-500 font-mono">
                  (Field-verified &bull; No invented claims)
                </span>
              </div>
              <p
                data-testid="story-caption"
                className="text-base sm:text-lg md:text-xl font-bold text-white tracking-tight leading-snug mt-1 font-sans"
              >
                {currentCaption || 'Loading verifiable execution state...'}
              </p>
            </div>
          </div>

          {/* Footer Controls & Progress Info */}
          <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-slate-800/80 text-xs text-slate-400">
            <div className="flex items-center gap-3">
              <span className="font-semibold text-slate-300">
                Slide {navState.slideIndex + 1} of {TOTAL_SLIDES}:
              </span>
              <span className="text-cyan-300 font-mono">{currentSlide.slideTitle}</span>
            </div>

            <div className="flex items-center gap-4">
              {/* Auto advance timer */}
              <div className="flex items-center gap-1.5 font-mono text-[11px] text-slate-400">
                <span>{navState.isPlaying ? 'Advancing in' : 'Paused at'}</span>
                <span className="font-bold text-white">{timerSecondsRemaining}s</span>
              </div>

              {/* Navigation Arrows */}
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => dispatch({ type: 'PREV_SLIDE' })}
                  disabled={navState.slideIndex === 0}
                  className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-white disabled:opacity-30 disabled:cursor-not-allowed font-medium"
                >
                  &larr; Prev
                </button>
                <button
                  type="button"
                  onClick={() => dispatch({ type: 'TOGGLE_PLAY' })}
                  className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-cyan-300 font-medium"
                >
                  {navState.isPlaying ? 'Pause' : 'Play'}
                </button>
                <button
                  type="button"
                  onClick={() => dispatch({ type: 'NEXT_SLIDE' })}
                  disabled={navState.slideIndex >= TOTAL_SLIDES - 1}
                  className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-white disabled:opacity-30 disabled:cursor-not-allowed font-medium"
                >
                  Next &rarr;
                </button>
              </div>

              {/* Skip to live */}
              <Link
                to="/live"
                className="text-cyan-400 hover:text-cyan-300 font-bold flex items-center gap-1 ml-2"
              >
                <span>Live Mission Control</span>
                <ExternalLink className="h-3 w-3" />
              </Link>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default StoryRoute;

import React from 'react';
import { Link } from 'react-router-dom';
import { Play, Pause, ChevronLeft, ChevronRight, ExternalLink } from 'lucide-react';
import { STORY_SLIDES, TOTAL_SLIDES } from '../utils/storyNavigation';

interface Props {
  slideIndex: number;
  isPlaying: boolean;
  onNext: () => void;
  onPrev: () => void;
  onTogglePlay: () => void;
  onSelectAct: (act: 1 | 2 | 3) => void;
}

export const StoryProgressBar: React.FC<Props> = ({
  slideIndex,
  isPlaying,
  onNext,
  onPrev,
  onTogglePlay,
  onSelectAct,
}) => {
  const currentSlide = STORY_SLIDES[slideIndex] || STORY_SLIDES[0];
  const progressPercent = Math.min(100, Math.round(((slideIndex + 1) / TOTAL_SLIDES) * 100));

  const acts: Array<{ act: 1 | 2 | 3; title: string }> = [
    { act: 1, title: 'Act 1: Say the rules' },
    { act: 2, title: 'Act 2: Watch it work' },
    { act: 3, title: 'Act 3: Try to break it' },
  ];

  return (
    <header className="w-full bg-slate-900 border-b border-slate-800 text-white select-none">
      {/* Top row: Brand, Acts navigation, and Skip to live */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-2.5 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <div className="h-3.5 w-3.5 bg-cyan-400 rounded-xs transform rotate-45" />
            <span className="font-bold tracking-tight text-white text-sm sm:text-base">Vasooli Story</span>
          </div>

          {/* Act Pills */}
          <div className="hidden md:flex items-center gap-1 bg-slate-800/80 p-0.5 rounded-lg border border-slate-700/60">
            {acts.map(({ act, title }) => {
              const isActive = currentSlide.act === act;
              return (
                <button
                  key={act}
                  type="button"
                  onClick={() => onSelectAct(act)}
                  className={`px-3 py-1 rounded-md text-xs font-semibold transition ${
                    isActive
                      ? 'bg-cyan-500 text-slate-950 shadow-xs'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'
                  }`}
                >
                  {title}
                </button>
              );
            })}
          </div>
        </div>

        {/* Controls and Skip to Live */}
        <div className="flex items-center gap-3">
          {/* Play/Pause & Step Controls */}
          <div className="flex items-center gap-1 bg-slate-800/80 px-2 py-1 rounded-lg border border-slate-700/60">
            <button
              type="button"
              onClick={onPrev}
              disabled={slideIndex === 0}
              className="p-1 text-slate-300 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
              title="Previous (Left Arrow)"
              aria-label="Previous slide"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={onTogglePlay}
              className="flex items-center gap-1.5 px-2 py-0.5 text-xs font-bold text-cyan-300 hover:text-white"
              title="Spacebar to toggle Play/Pause"
            >
              {isPlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
              <span className="hidden sm:inline">{isPlaying ? 'Pause' : 'Play'}</span>
            </button>
            <button
              type="button"
              onClick={onNext}
              disabled={slideIndex >= TOTAL_SLIDES - 1}
              className="p-1 text-slate-300 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
              title="Next (Right Arrow)"
              aria-label="Next slide"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>

          {/* Keyboard hints badge */}
          <span className="hidden lg:inline-flex items-center gap-1.5 text-[11px] text-slate-400 font-mono">
            <kbd className="px-1.5 py-0.5 bg-slate-800 border border-slate-700 rounded text-slate-300 text-[10px]">Space</kbd>
            <span>pause</span>
            <kbd className="px-1.5 py-0.5 bg-slate-800 border border-slate-700 rounded text-slate-300 text-[10px]">← / →</kbd>
            <span>navigate</span>
          </span>

          {/* Skip to live link */}
          <Link
            to="/live"
            className="flex items-center gap-1 px-3 py-1 bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg text-xs font-bold transition shadow-xs"
            title="Jump straight to live mission control"
          >
            <span>Skip to live</span>
            <ExternalLink className="h-3 w-3" />
          </Link>
        </div>
      </div>

      {/* Progress Bar Line */}
      <div className="w-full bg-slate-800 h-1">
        <div
          className="bg-cyan-400 h-1 transition-all duration-300 ease-out"
          style={{ width: `${progressPercent}%` }}
        />
      </div>
    </header>
  );
};


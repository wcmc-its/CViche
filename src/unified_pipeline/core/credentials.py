"""Degree and credential post-nominals, as a CV writes them after a name:
the list from the faculty affairs system that `personal_info_extractor`
looks for on a CV's name line, and stage 5 refuses as a co-author (#1661)."""

from typing import Final

# Includes both with periods (all degrees) and without periods (common, unambiguous ones only)
CREDENTIALS: Final[tuple[str, ...]] = (
    # Titles
    'Dr.', 'Professor', 'Prof.',

    # Doctoral degrees (with and without periods)
    'Ph.D.', 'PhD', 'M.D.', 'MD', 'D.M.D.', 'DMD', 'D.D.S.', 'DDS',
    'D.O.', 'DO', 'Psy.D.', 'PsyD', 'Ed.D.', 'EdD', 'J.D.', 'JD',
    'Pharm.D.', 'PharmD', 'D.V.M.', 'DVM', 'V.M.D.', 'VMD',
    'D.P.M.', 'DPM', 'Au.D.', 'AuD', 'D.N.P.', 'DNP',
    'Sc.D.', 'ScD', 'D.Sc.', 'DSc', 'D.Phil.', 'DPhil',
    'Dr.P.H.', 'DrPH', 'D.P.H.', 'DPH', 'D.S.W.', 'DSW',
    'D.M.A.', 'DMA', 'D.P.T.', 'DPT', 'D.H.Sc.', 'DHSc',
    'D.M.', 'DM', 'D.M.Sc.', 'DMSc',

    # Medical credentials
    'M.B.', 'MB', 'M.B.S.', 'MBS', 'M.B.B.S.', 'MBBS',
    'B.A.O.', 'M.Med.', 'D.C.', 'D.C.H.',
    'Dr. Med.', 'Dr.sc.hum.', 'Dr. rer. medic.', 'Dr. rer. nat.', 'Dr. techn.',

    # Master's degrees (with and without periods for very common ones)
    'M.S.', 'MS', 'M.A.', 'MA', 'M.Sc.', 'MSc', 'M.B.A.', 'MBA',
    'M.P.H.', 'MPH', 'M.S.N.', 'MSN', 'M.S.W.', 'MSW', 'M.Phil.', 'MPhil',
    'M.E.', 'ME', 'M.Eng', 'MEng', 'M.Ed.', 'MEd', 'M.F.A.', 'MFA',
    'M.M.', 'MM', 'M.P.A.', 'MPA', 'M.H.A.', 'MHA', 'M.L.S.', 'MLS',
    'M.M.S.', 'MMS', 'M.M.Sc.', 'MMSc', 'M.H.S.', 'MHS',
    'M.P.A.S.', 'MPAS', 'M.S.P.H.', 'MSPH', 'M.P.T.', 'MPT',
    'M.Tech.', 'MTech', 'M.Pharm.', 'MPharm', 'M.Res.', 'MRes',
    'M.A.S.', 'MAS', 'M.S.E.', 'MSE', 'M.E.E.', 'MEE',
    'M.Ch.', 'M.Div.', 'M.H.P.E.', 'M.L.I.S.', 'M.P.P.',
    'M.A.T.', 'M.V.Sc.', 'M.H.Sc.', 'M.S.C.R.', 'M.T.S.',
    'M.E.H.P.', 'M.S.H.S.', 'M.S.H.P.', 'M.S.Ed.', 'M.H.C.M.',
    'M.M. (Music)', 'M.M.E.', 'M.M.M.', 'M.O.T.', 'M.P.S.',
    'M.S.C.E.', 'M.S.C.I.', 'M.S.J.', 'M.S.P.', 'M.S.S.A.',
    'M.S.T.R.', 'M.Sc.D.', 'M.Sci.', 'M.Sc.B.M.I.',
    'M.C.E.', 'M.C.M.Sc.', 'M.Ch.E.', 'M.Bioch.', 'M.BE.',
    'M.Ag.S.', 'M.I.A.', 'M.L.A.S.', 'M.Phys', 'M.Th.',
    'M.U.Dr.', 'M.H.M.', 'S.M.', 'S.M.D.', 'A.M.', 'A.L.M.',
    'Ed.M.', 'E.M.L.', 'Sc.M.', 'Ph.M.', 'Psy.M.', 'Th.M.',

    # Bachelor's degrees (with periods only - too risky without)
    'B.S.', 'B.A.', 'B.Sc.', 'B.M.', 'B.E.', 'B.Eng.',
    'B.S.N.', 'BSN', 'B.F.A.', 'B.B.A.', 'B.S.E.', 'B.Med.Sc.',
    'Bs.C.', 'A.B.', 'S.B.', 'Sc.B.',
    'B.A.S.', 'B.M.S.', 'B.Tech.', 'B.V.S.', 'B.V.Sc.',
    'B.E.E.', 'B.Ch.E.', 'B.D.S.', 'B.Pharm.', 'Pharm.B.',
    'B.Ed.', 'B.G.S.', 'B.H.A.', 'B.H.K.', 'B.L.A.',
    'B.M.E.', 'B.N.S.', 'B.Ph.', 'B.S.D.', 'B.S.E.E.',
    'B.S.F.S.', 'B.S.M.T.', 'B.S.Ch.E.', 'B.Sc.H.',
    'B.M. (Music)', 'B.Mgt.', 'B.E.S.', 'B.A.Ed.', 'B.B',
    'B.C.S.',

    # Associate degrees
    'A.A.', 'A.S.', 'A.A.S.', 'A.M.L.S.', 'A.C.S.W.',

    # Other professional credentials
    'FRCSC', 'R.N.', 'RN', 'O.D.', 'OD', 'O.T.R.',
    'L.L.B.', 'LLB', 'L.L.M.', 'LLM', 'L.M.C.D.',
    'H.S.C.', 'I.Sc.', 'C.T.F.', 'F.A.',

    # Special degrees
    'Diplom', 'Diploma', 'D.Bioethics', 'D. Tech.', 'D.Eng',
    'D.H.Ed.', 'D.Min', 'D.P.S.', 'D.T.M.&H',
    'D.Sc. (honoris causa)', 'D.Sc.Hyg.',
    'Degree Info Pending'
)

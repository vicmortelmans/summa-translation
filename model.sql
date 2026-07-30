PRAGMA foreign_keys = ON;


------------------------------------------------------------
-- Source texts
------------------------------------------------------------

CREATE TABLE LatinByLemma (
    Id TEXT PRIMARY KEY,
    Text TEXT NOT NULL,
    ExtractTermsStatus INTEGER NOT NULL DEFAULT 0
        CHECK (ExtractTermsStatus IN (0,1))
);


CREATE TABLE DutchByLemma (
    Id TEXT PRIMARY KEY,
    Text TEXT NOT NULL
);


CREATE TABLE GermanByArticle (
    Id TEXT PRIMARY KEY,
    Text TEXT NOT NULL
);


CREATE TABLE GermanByLemma (
    Id TEXT PRIMARY KEY,
    Text TEXT NOT NULL,

    FOREIGN KEY (Id)
        REFERENCES LatinByLemma(Id)
        ON DELETE CASCADE
);



------------------------------------------------------------
-- Terminology
------------------------------------------------------------

CREATE TABLE TerminologySet (
    LatinWord TEXT NOT NULL,
    DutchWord TEXT NOT NULL,
    LatinContext TEXT,
    DutchContext TEXT,

    PRIMARY KEY (LatinWord, DutchWord)
);



CREATE TABLE TerminologyByTerm (
    LatinStam TEXT NOT NULL,
    DutchStam TEXT NOT NULL,
    Count INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (LatinStam, DutchStam)
);



CREATE TABLE TerminologyByTermExamples (
    Id INTEGER PRIMARY KEY,

    LatinStam TEXT NOT NULL,
    DutchStam TEXT NOT NULL,

    LatinContext TEXT,
    DutchContext TEXT,

    FOREIGN KEY (LatinStam, DutchStam)
        REFERENCES TerminologyByTerm(LatinStam, DutchStam)
        ON DELETE CASCADE
);



------------------------------------------------------------
-- Links between lemmas and terminology
------------------------------------------------------------

CREATE TABLE TermSetByLemma (
    Id TEXT NOT NULL,
    LatinWord TEXT NOT NULL,

    PRIMARY KEY (Id, LatinWord),

    FOREIGN KEY (Id)
        REFERENCES LatinByLemma(Id)
        ON DELETE CASCADE
);



CREATE TABLE TermsByTermByLemma (
    Id TEXT NOT NULL,

    LatinStam TEXT NOT NULL,
    DutchStam TEXT NOT NULL,

    LatinContext TEXT,
    DutchContext TEXT,

    Count INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (Id, LatinStam, DutchStam),

    FOREIGN KEY (Id)
        REFERENCES LatinByLemma(Id)
        ON DELETE CASCADE,

    FOREIGN KEY (LatinStam, DutchStam)
        REFERENCES TerminologyByTerm(LatinStam, DutchStam)
);



------------------------------------------------------------
-- Indexes
------------------------------------------------------------

CREATE INDEX idx_termsetbylemma_latinword
    ON TermSetByLemma(LatinWord);


CREATE INDEX idx_termsbylemma_term
    ON TermsByTermByLemma(LatinStam, DutchStam);


CREATE INDEX idx_termsbylemma_id
    ON TermsByTermByLemma(Id);


CREATE INDEX idx_examples_term
    ON TerminologyByTermExamples(LatinStam, DutchStam);
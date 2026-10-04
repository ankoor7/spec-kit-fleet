# Tasks: Photo albums

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Create project structure per implementation plan
- [ ] T002 [P] Configure linting in tools/lint.config.json

## Phase 2: Foundational (Blocking Prerequisites)

- [ ] T003 Create base Album entity in src/models/album.py
- [ ] T004 [P] Set up storage adapter in src/storage/adapter.py

---

## Phase 3: User Story 1 - Create an album (Priority: P1) 🎯 MVP

### Tests for User Story 1

- [ ] T005 [P] [US1] Contract test for POST /albums in tests/contract/test_albums.py

### Implementation for User Story 1

- [ ] T006 [US1] Implement AlbumService.create in src/services/albums.py
- [ ] T007 [US1] Add POST /albums endpoint in src/api/albums.py

## Phase 4: User Story 2 - Share an album (Priority: P2)

- [ ] T008 [P] [US2] Create Share entity in src/models/share.py
- [ ] T009 [US2] Implement ShareService in src/services/shares.py
- [ ] T010 [US2] Add POST /albums/{id}/shares in src/api/shares.py
- [ ] T011 [US2] Integrate with User Story 1 components in src/services/albums.py

## Phase 5: Polish & Cross-Cutting Concerns

- [ ] T012 [P] Documentation updates in docs/albums.md

## Dependencies & Execution Order

- Foundational blocks all user stories.

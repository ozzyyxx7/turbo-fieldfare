import Testing
@testable import TurboFieldfare

@Suite struct PrefillRoutedTileSchedulerTests {
    @Test func halfCacheIssuesFirstTileWithoutPendingWork() {
        let decision = PrefillRoutedTileScheduler().decide(
            PrefillRoutedTileSchedulerInput(hasPendingTile: false,
                                            pendingAssignedSlots: [],
                                            avoidingSlotPlanAvailable: false))

        #expect(decision == .issueWithoutPending)
    }

    @Test func halfCachePrefetchesWhenAvoidingSlotPlanExists() {
        let decision = PrefillRoutedTileScheduler().decide(
            PrefillRoutedTileSchedulerInput(hasPendingTile: true,
                                            pendingAssignedSlots: [3, 7, 8],
                                            avoidingSlotPlanAvailable: true))

        #expect(decision == .prefetchNext(avoidingSlots: [3, 7, 8]))
    }

    @Test func halfCacheDrainsWhenPendingTileHasNoAssignedSlots() {
        let decision = PrefillRoutedTileScheduler().decide(
            PrefillRoutedTileSchedulerInput(hasPendingTile: true,
                                            pendingAssignedSlots: [],
                                            avoidingSlotPlanAvailable: true))

        #expect(decision == .drainBeforeIssue(reason: .pendingTileHasNoAssignedSlots))
    }

    @Test func halfCacheDrainsWhenAvoidingSlotPlanIsUnavailable() {
        let decision = PrefillRoutedTileScheduler().decide(
            PrefillRoutedTileSchedulerInput(hasPendingTile: true,
                                            pendingAssignedSlots: [1, 2],
                                            avoidingSlotPlanAvailable: false))

        #expect(decision == .drainBeforeIssue(reason: .avoidingSlotPlanUnavailable))
    }

    @Test func schedulerAllowsSecondLookaheadWhenDepthBudgetAllowsIt() {
        let scheduler = PrefillRoutedTileScheduler(
            config: PrefillRoutedTileSchedulerConfig(maxPendingDepth: 2, tileExperts: 4))

        let decision = scheduler.decide(PrefillRoutedTileSchedulerInput(
            hasPendingTile: true,
            pendingDepth: 2,
            pendingAssignedSlots: [1, 2, 3, 4, 5, 6, 7, 8],
            avoidingSlotPlanAvailable: true))

        #expect(decision == .prefetchNext(avoidingSlots: [1, 2, 3, 4, 5, 6, 7, 8]))
    }

    @Test func schedulerDrainsAtConfiguredPendingDepth() {
        let decision = PrefillRoutedTileScheduler(
            config: PrefillRoutedTileSchedulerConfig(maxPendingDepth: 2, tileExperts: 4)).decide(
            PrefillRoutedTileSchedulerInput(hasPendingTile: true,
                                            pendingDepth: 3,
                                            pendingAssignedSlots: [1, 2],
                                            avoidingSlotPlanAvailable: true))

        #expect(decision == .drainBeforeIssue(reason: .maxPendingDepthReached))
    }

    @Test func schedulerConfigValidatesSlotBudget() {
        let defaultConfig = PrefillRoutedTileSchedulerConfig()
        let depthTwoFourExperts = PrefillRoutedTileSchedulerConfig(maxPendingDepth: 2, tileExperts: 4)
        let depthTwoEightExperts = PrefillRoutedTileSchedulerConfig(maxPendingDepth: 2, tileExperts: 8)

        #expect(defaultConfig.fitsSlotBudget(slotCount: 16))
        #expect(depthTwoFourExperts.fitsSlotBudget(slotCount: 16))
        #expect(!depthTwoEightExperts.fitsSlotBudget(slotCount: 16))
    }

    @Test func schedulerConfigFitsTileWidthToPublicSlotBudgets() {
        let config = PrefillRoutedTileSchedulerConfig()

        #expect(config.fitted(toSlotCount: 8)?.tileExperts == 4)
        #expect(config.fitted(toSlotCount: 8)?.maxPendingDepth == 1)
        for slots in [16, 24, 32, 64] {
            #expect(config.fitted(toSlotCount: slots)?.tileExperts == 8)
        }
        #expect(config.fitted(toSlotCount: 1) == nil)
        #expect(config.fitted(toSlotCount: 0) == nil)
    }

    @Test func halfCachePreservesPendingSlotAvoidanceOrder() {
        let pendingSlots = [9, 1, 9, 3]
        let decision = PrefillRoutedTileScheduler().decide(
            PrefillRoutedTileSchedulerInput(hasPendingTile: true,
                                            pendingAssignedSlots: pendingSlots,
                                            avoidingSlotPlanAvailable: true))

        #expect(decision == .prefetchNext(avoidingSlots: pendingSlots))
    }

}

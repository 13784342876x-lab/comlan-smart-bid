from __future__ import annotations

from app.human_confirmations import HumanConfirmationCommand
from app.project_facts import ProjectFactCommand
from app.web.command_dtos import HumanConfirmationCommandDTO, ProjectFactCommandDTO
from app.web.command_service import PreconditionRequiredError
from app.web.dtos import HumanConfirmationMutationResultDTO, ProjectFactMutationResultDTO
from app.web.writing_preparation_query_service import WritingPreparationReadService


class WritingContextCommandService:
    """HTTP adapter over canonical pre-writing fact and choice services."""

    def __init__(self, read_service: WritingPreparationReadService) -> None:
        self.read_service = read_service
        self.application = read_service.fact_service
        self.confirmation_application = read_service.confirmation_service

    def confirm_fact(
        self,
        project_id: str,
        fact_id: str,
        command: ProjectFactCommandDTO,
        if_match: str | None,
    ) -> ProjectFactMutationResultDTO:
        if not if_match:
            raise PreconditionRequiredError("Project Fact mutation requires If-Match")
        with self.read_service.mutation_lock:
            self.read_service.assert_current_token(project_id, if_match)
            self.application.confirm(
                project_id,
                fact_id,
                ProjectFactCommand(
                    status=command.status,
                    value=command.value,
                    source_description=command.source_description,
                    confirmation_basis=command.confirmation_basis,
                    note=command.note,
                    change_reason=command.change_reason,
                ),
                self.application.snapshot(project_id).token,
            )
            preparation = self.read_service.get(project_id)
        return ProjectFactMutationResultDTO(
            updated_fact_id=fact_id,
            version_token=preparation.version_token,
            writing_preparation=preparation,
            writing_context=preparation.writing_context,
        )

    def decide_confirmation(
        self,
        project_id: str,
        confirmation_id: str,
        command: HumanConfirmationCommandDTO,
        if_match: str | None,
    ) -> HumanConfirmationMutationResultDTO:
        if not if_match:
            raise PreconditionRequiredError("Human Confirmation mutation requires If-Match")
        with self.read_service.mutation_lock:
            self.read_service.assert_current_token(project_id, if_match)
            self.confirmation_application.decide(
                project_id,
                confirmation_id,
                HumanConfirmationCommand(
                    answer=command.answer,
                    source_description=command.source_description,
                    confirmation_basis=command.confirmation_basis,
                    note=command.note,
                    change_reason=command.change_reason,
                ),
                self.confirmation_application.snapshot(project_id).token,
            )
            preparation = self.read_service.get(project_id)
            confirmation = self.read_service.confirmation(project_id, confirmation_id)
        return HumanConfirmationMutationResultDTO(
            updated_confirmation_id=confirmation_id,
            version_token=preparation.version_token,
            confirmation=confirmation,
            writing_preparation=preparation,
            writing_context=preparation.writing_context,
        )
